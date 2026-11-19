from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from scripts.audit_cuda_bundle import audit
from scripts.check_torch_cuda import check


class FakeTensor:
    def __matmul__(self, other):
        return self

    def sum(self):
        return self

    def item(self):
        return 8.0


def fake_torch(version="2.8.0+cu128", cuda_version="12.8", available=True, fail=None):
    def ones(*args, **kwargs):
        if fail:
            raise RuntimeError(fail)
        return FakeTensor()
    return SimpleNamespace(
        __version__=version, version=SimpleNamespace(cuda=cuda_version),
        cuda=SimpleNamespace(is_available=lambda: available, device_count=lambda: 1,
                             synchronize=lambda: None, get_device_name=lambda _: "RTX"),
        inference_mode=nullcontext, ones=ones, float16=object(),
    )


def test_compatible_cuda_torch_is_reused():
    assert check(fake_torch())[0] == 0
    assert check(fake_torch("2.7.1+cu126", "12.6"))[0] == 0
    assert check(fake_torch("2.6.0+cu124", "12.4"))[0] == 0


def test_cpu_or_old_torch_requests_cuda_wheel():
    assert check(fake_torch(cuda_version=None))[0] == 2
    assert check(fake_torch(version="2.5.1+cu121"))[0] == 2


def test_incompatible_kernel_stops_without_repeated_download():
    assert check(fake_torch(fail="CUDA error: no kernel image is available"))[0] == 4
    assert check(fake_torch(fail="CUDA error: invalid device function"))[0] == 4


def test_driver_failure_does_not_redownload_torch():
    assert check(fake_torch(available=False))[0] == 3
    assert check(fake_torch(fail="CUDA driver not initialized"))[0] == 3


def test_packaging_runner_requires_cuda_wheel_without_running_kernels():
    no_gpu = fake_torch("2.6.0+cu124", "12.4", available=False, fail="no GPU")
    assert check(no_gpu, package_only=True)[0] == 0
    assert check(no_gpu)[0] == 3
    assert check(fake_torch(cuda_version=None), package_only=True)[0] == 2


def test_bundle_audit_counts_exact_cuda_dll_duplicates(tmp_path):
    (tmp_path / "torch" / "lib").mkdir(parents=True)
    (tmp_path / "nvidia").mkdir()
    (tmp_path / "torch" / "lib" / "cudnn_a.dll").write_bytes(b"same")
    (tmp_path / "nvidia" / "cudnn_b.DLL").write_bytes(b"same")
    (tmp_path / "nvidia" / "cublas.dll").write_bytes(b"other")
    (tmp_path / "nvidia" / "unrelated.dll").write_bytes(b"same")
    total, duplicated, groups = audit(tmp_path)
    assert total == 13 and duplicated == 4 and len(groups) == 1
