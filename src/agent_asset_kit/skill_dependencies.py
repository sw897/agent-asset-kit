"""Offline validation of portable skill capability declarations."""

import re

from .assetlib import ROOT, Stop, relative


MCP_FIELDS = {'service', 'mode', 'via', 'tools', 'optional', 'reference'}


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}', value))


def mcp_specs(record):
    """Describe required capabilities without resolving accounts or connections."""
    values = record.get('requirements', {}).get('mcp', [])
    if not isinstance(values, list):
        raise Stop('requirements.mcp must be a list')
    seen = set()
    for spec in values:
        if not isinstance(spec, dict) or set(spec) != MCP_FIELDS:
            raise Stop('MCP declarations require only service, mode, via, tools, optional and reference')
        if (not identifier(spec['service']) or not identifier(spec['mode']) or
                spec['via'] not in ('native', 'cli') or type(spec['optional']) is not bool):
            raise Stop('invalid MCP service, mode, via or optional flag')
        tools = spec['tools']
        if not isinstance(tools, list) or not tools or any(not identifier(t) for t in tools) or len(tools) != len(set(tools)):
            raise Stop('MCP tools must be a nonempty list of distinct original capability names')
        identity = (spec['service'], spec['mode'])
        if identity in seen:
            raise Stop('duplicate MCP service/mode declaration')
        seen.add(identity)
        if not isinstance(spec['reference'], str):
            raise Stop('MCP reference must be a relative file path')
        ref = relative(spec['reference'])
        path = ROOT / record['path'] / ref
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to((ROOT / record['path']).resolve()):
            raise Stop('MCP reference must be an existing regular file inside its skill')
    return values


def contracts(manifest):
    issues = []
    for record in manifest['packages'].values():
        try:
            mcp_specs(record)
        except (Stop, TypeError, ValueError):
            issues.append({'skill': record['name'], 'code': 'INVALID_MCP_DECLARATION', 'severity': 'error'})
    return issues
