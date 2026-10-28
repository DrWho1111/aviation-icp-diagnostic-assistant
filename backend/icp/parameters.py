"""Parse explicit mission time without silently dropping signs or guessing ranges."""
import math
import re

TIME = re.compile(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(小时|分钟|天|h\b)',re.I)


def mission_hours(query, explicit=None):
    if explicit is not None:
        if isinstance(explicit,bool): raise ValueError('Invalid mission time')
        value=float(explicit)
    else:
        matches=list(TIME.finditer(query))
        if not matches: return None
        if len(matches)!=1: raise ValueError('Specify one mission time')
        match=matches[0]
        if re.search(r'\d\s*(?:至|到|~|～|—|–)\s*$',query[:match.start()]):
            raise ValueError('Time range is ambiguous')
        if match[1].startswith('-') and re.search(r'\d\s*$',query[:match.start()]):
            raise ValueError('Time range is ambiguous')
        value=float(match[1])
        if re.search(r'负\s*$',query[:match.start()]): value=-value
        value *= {'分钟':1/60,'天':24}.get(match[2],1)
    if not math.isfinite(value) or value<0: raise ValueError('Invalid mission time')
    return value
