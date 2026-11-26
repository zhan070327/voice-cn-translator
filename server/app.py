"""SameFreq Cloud API for the desktop client."""
from asyncio import Semaphore, wait_for
from base64 import b64encode
from contextlib import asynccontextmanager
from dataclasses import asdict
from io import BytesIO
import logging
import re
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
import qrcode
from qrcode.image.pure import PyPNGImage
from starlette.concurrency import run_in_threadpool

from .auth import Auth, hash_password
from .azure import Azure, MAX_AUDIO_BYTES, SAMPLE_RATE, SPEECH_LOCALES, UpstreamError, validated_audio
from .payments import Alipay, Channel, PaymentError, WeChat
from .settings import Settings
from .store import Denied, Store, digest

LOG = logging.getLogger("uvicorn.error").getChild("samefreq")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Input):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", value):
            raise ValueError("Expected an email address")
        return value


class Refresh(Input):
    refresh_token: str = Field(min_length=20, max_length=128, repr=False)


class Translation(Input):
    text: str = Field(min_length=1, max_length=2000)
    source: str = Field(max_length=8)
    target: str = Field(max_length=8)


class Order(Input):
    plan_id: str = Field(max_length=32)
    channel: str = Field(max_length=32)


CALLBACK_MAX_BYTES = 64 * 1024


async def read_bounded(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise Denied(413, "body_too_large")
        body.extend(chunk)
    return bytes(body)


def create_app(settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
    cfg = settings if settings is not None else Settings.from_env()
    store = Store(cfg.database)
    auth = Auth(cfg, store)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
                                      limits=httpx.Limits(max_connections=32, max_keepalive_connections=16)) as client:
            app.state.azure = Azure(cfg, client)
            registry: dict[str, Channel] = {"wechat": WeChat(cfg.secrets, client), "alipay": Alipay(cfg.secrets, client)}
            app.state.channels = registry
            app.state.inflight = Semaphore(24)
            yield

    app = FastAPI(title="SameFreq Cloud", lifespan=lifespan)

    @app.middleware("http")
    async def body_limits(request, call_next):
        if request.method == "POST":
            path = request.url.path
            raw = path == "/v1/speech" or path.startswith("/v1/payments/")
            limit = (MAX_AUDIO_BYTES if path == "/v1/speech" else CALLBACK_MAX_BYTES if raw else 16 * 1024)
            length = request.headers.get("content-length")
            if not raw and (length is None or "transfer-encoding" in request.headers):
                return JSONResponse({"code": "length_required"}, status_code=411)
            if length is not None and (not re.fullmatch(r"\d{1,9}", length) or int(length) > limit):
                return JSONResponse({"code": "body_too_large"}, status_code=413)
        response = await call_next(request)
        if request.url.path.startswith("/v1"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Denied)
    async def rejected(_request, error):
        return JSONResponse({"code": error.code}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _error):
        return JSONResponse({"code": "invalid_input"}, status_code=422)

    def account(authorization: Annotated[str, Header()] = ""):
        return auth.identify(authorization)

    def throttle_auth(request, email=""):
        host = request.client.host if request.client else "unknown"
        limits = [("auth:global", cfg.auth_per_minute * 20), ("auth:ip:" + host, cfg.auth_per_minute)]
        if email:
            limits.append(("auth:email:" + digest(email), cfg.auth_per_minute))
        store.rate(*limits)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/v1/auth/register", status_code=201)
    def register(data: Credentials, request: Request):
        throttle_auth(request, data.email)
        if len(data.password) < 10:
            raise Denied(422, "invalid_input")
        user = store.register(data.email, hash_password(data.password))
        LOG.info("Account registered account=%s", user)
        return auth.new_session(user)

    @app.post("/v1/auth/login")
    def login(data: Credentials, request: Request):
        throttle_auth(request, data.email)
        try:
            user = auth.verify_password(data.email, data.password)
        except Denied:
            LOG.info("Account login rejected")
            raise
        LOG.info("Account login account=%s", user)
        return auth.new_session(user)

    @app.post("/v1/auth/refresh")
    def refresh(data: Refresh, request: Request):
        throttle_auth(request)
        return auth.refresh(data.refresh_token)

    @app.post("/v1/auth/logout")
    def logout(data: Refresh, request: Request):
        throttle_auth(request)
        auth.logout(data.refresh_token)
        return {"ok": True}

    @app.get("/v1/entitlement")
    @app.get("/v1/subscription")
    def entitlement(user: str = Depends(account)):
        return store.entitlement(user, int(time.time()))

    @app.get("/v1/plans")
    def plans(user: str = Depends(account)):
        return [asdict(p) for p in cfg.plans.values()]

    async def online(user, kind, units, charge_ms, process, *args):
        await run_in_threadpool(store.rate, ("online:" + user, cfg.online_per_minute),
                                ("online:global", cfg.global_per_minute))
        if app.state.inflight.locked():
            raise Denied(429, "rate_limited")
        async with app.state.inflight:
            reservation = await run_in_threadpool(store.reserve, user, kind, units, charge_ms, int(time.time()))
            success = False
            try:
                result = await wait_for(process(*args), timeout=cfg.upstream_timeout)
                success = True
                return result
            except (UpstreamError, TimeoutError):
                raise Denied(503, "unavailable") from None
            finally:
                await run_in_threadpool(store.finalize, reservation, success)

    @app.post("/v1/speech")
    async def speech(request: Request, language: str = "auto", user: str = Depends(account)):
        if language != "auto" and language not in SPEECH_LOCALES:
            raise Denied(422, "invalid_input")
        if request.headers.get("content-type") != "audio/wav":
            raise Denied(415, "invalid_input")
        try:
            audio, frames = validated_audio(await read_bounded(request, MAX_AUDIO_BYTES))
        except ValueError:
            raise Denied(422, "invalid_input") from None
        elapsed_ms = (frames * 1000 + SAMPLE_RATE - 1) // SAMPLE_RATE
        return await online(user, "asr", frames, elapsed_ms, app.state.azure.transcribe, audio, language)

    @app.post("/v1/translate")
    async def translate(data: Translation, user: str = Depends(account)):
        text = data.text.strip()
        if not text or data.source not in SPEECH_LOCALES or data.target not in SPEECH_LOCALES:
            raise Denied(422, "invalid_input")
        units = len(text)
        return await online(user, "translation", units, units * cfg.text_ms_per_character,
                            app.state.azure.translate, text, data.source, data.target)

    @app.post("/v1/orders", status_code=201)
    async def create_order(data: Order, user: str = Depends(account)):
        channel = app.state.channels.get(data.channel)
        plan = cfg.plans.get(data.plan_id)
        if not channel or not plan:
            raise Denied(422, "invalid_input")
        await run_in_threadpool(store.rate, ("orders:" + user, cfg.orders_per_minute),
                                ("orders:global", cfg.orders_per_minute * 20))
        order_id = await run_in_threadpool(store.order, user, plan, data.channel, int(time.time()))
        try:
            pay_url = await wait_for(channel.create(order_id, plan.name, plan.price_cents), timeout=9)
        except (PaymentError, TimeoutError):
            LOG.warning("Payment order gateway failure order=%s channel=%s", order_id, data.channel)
            raise Denied(503, "unavailable") from None
        output = BytesIO()
        qrcode.make(pay_url, image_factory=PyPNGImage, box_size=5).save(output)
        LOG.info("Payment order created order=%s channel=%s account=%s", order_id, data.channel, user)
        return {"id": order_id, "pay_url": pay_url, "qr_png_base64": b64encode(output.getvalue()).decode()}

    @app.get("/v1/orders/{order_id}")
    def order_status(order_id: str, user: str = Depends(account)):
        return store.order_status(user, order_id)

    @app.post("/v1/payments/{channel_name}/callback")
    async def callback(channel_name: str, request: Request):
        channel = app.state.channels.get(channel_name)
        if not channel:
            raise Denied(404, "invalid_input")
        now = int(time.time())
        try:
            notice = channel.verify(request.headers, await read_bounded(request, CALLBACK_MAX_BYTES), now)
        except PaymentError:
            LOG.warning("Payment callback rejected channel=%s", channel_name)
            raise Denied(400, "invalid_signature_or_notice") from None
        try:
            fresh = await run_in_threadpool(store.settle, channel_name, notice, now)
        except Denied:
            LOG.warning("Payment callback mismatch channel=%s order=%s", channel_name, notice.order_id)
            raise
        LOG.info("Payment callback accepted channel=%s order=%s new=%s", channel_name, notice.order_id, fresh)
        return PlainTextResponse("success") if channel_name == "alipay" else Response(status_code=204)

    return app
