"""Select the channel embedded in this build; GitHub behavior stays separate."""
from valorant_translator.updates import domestic, github


def check_for_update(channel=None):
    channel = channel or domestic.load_channel()
    if channel["channel"] == "domestic":
        return domestic.find_update(channel)
    return github.find_update(channel["github_repository"])
