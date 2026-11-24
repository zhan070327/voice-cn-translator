"""Retain legacy channels and route the same dual installer to a reachable source."""
from dataclasses import replace

from valorant_translator.updates import domestic, github


def check_for_update(channel=None):
    channel = channel or domestic.load_channel()
    edition = channel.get("edition", "gpu")

    def github_release():
        repository = channel["github_repository"]
        return (github.find_update(repository) if edition == "gpu" else
                github.find_update(repository, edition=edition))

    if channel["channel"] == "domestic":
        return domestic.find_update(channel)
    if channel["channel"] == "github":
        return github_release()
    errors = []
    reached = False
    # The first usable update wins. A reachable but empty channel must not
    # hide a newer release on the other one during a paired publish.
    for source, check in (("domestic", lambda: domestic.find_update(channel)), ("github", github_release)):
        try:
            release = check()
        except (github.UpdateError, OSError) as exc:
            errors.append(str(exc))
        else:
            reached = True
            if release is not None:
                return replace(release, source=source)
    if reached:
        return None
    raise github.UpdateError("两个更新通道均无法连接：" + " / ".join(errors))
