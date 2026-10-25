"""Separate, versioned scene definitions and starting settings."""
import copy
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SHOWS={version:json.loads((ROOT/'shows'/(version+'.json')).read_text()) for version in ('v1','v2')}


def validate_version(version):
    if not isinstance(version,str) or version not in SHOWS:
        raise ValueError('演出はv1またはv2を選んでください')
    return version


def scene_for(version='v1'):
    return SHOWS[validate_version(version)]['scene']


def preset_for(version='v1'):
    return copy.deepcopy(SHOWS[validate_version(version)]['preset'])


def catalog():
    return {'default':'v1','shows':[copy.deepcopy(profile) for profile in SHOWS.values()]}
