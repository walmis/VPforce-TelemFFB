"""Pure data merge and transform utilities for XML config resolution.

No global state, no I/O — operates on list[dict] structures.
Extracted from xmlutils.py Cluster D.
"""
from typing import Any

from telemffb.xml.types import (
    ClassDataRow,
    DefaultDataRow,
    ModelDataRow,
    PartialDefaultDataRow,
    PrereqRow,
    ScOverrideRow,
)


def update_default_data_with_craft_result(
    defaultdata: list[DefaultDataRow],
    craftresult: list[ClassDataRow],
) -> list[DefaultDataRow]:
    """Merge class defaults into sim defaults by name."""
    updated = defaultdata.copy()
    for craft_item in craftresult:
        name = craft_item['name']
        matching = next((i for i in updated if i['name'] == name), None)
        if matching:
            matching['value'] = craft_item['value']
            matching['unit'] = craft_item['unit']
            matching['replaced'] = "Class Default"
    return updated


def update_data_with_models(
    defaults_data: list[DefaultDataRow],
    model_data: list[ModelDataRow | PartialDefaultDataRow],
    replacetext: str,
) -> list[DefaultDataRow]:
    """Merge model/user overrides into base data by name.

    Keeps the value this override is hiding as prior_value/prior_unit: since the
    layers merge in a fixed order, the topmost override's stash is what the
    setting would resolve to if that override were erased (used for auto-revert).
    """
    updated = defaults_data.copy()
    model_dict: dict[str, dict[str, str]] = {m['name']: {'value': m['value'], 'unit': m['unit']} for m in model_data}
    for item in updated:
        if item['name'] in model_dict:
            item['prior_value'] = item['value']
            item['prior_unit'] = item['unit']
            item['value'] = model_dict[item['name']]['value']
            item['unit'] = model_dict[item['name']]['unit']
            item['replaced'] = replacetext
    return updated


def update_sc_overrides_with_user(
    defaults_ovr: list[ScOverrideRow],
    user_ovr: list[ScOverrideRow],
) -> list[ScOverrideRow]:
    """Merge default + user SC overrides; user wins by name, new items appended."""
    updated = defaults_ovr.copy()
    for user_model in user_ovr:
        found = False
        for existing in updated:
            if existing['name'] == user_model['name']:
                existing['var'] = user_model['var']
                existing['sc_unit'] = user_model['sc_unit']
                existing['scale'] = user_model['scale']
                existing['source'] = 'user'
                found = True
                break
        if not found:
            updated.append({
                'name': user_model['name'],
                'var': user_model['var'],
                'sc_unit': user_model['sc_unit'],
                'scale': user_model['scale'],
                'source': 'user',
            })
    return updated


def remove_dicts_by_names(
    data_list: list[DefaultDataRow],
    removal_list: list[str],
) -> list[DefaultDataRow]:
    """Filter out dicts whose 'name' is in removal_list."""
    return [d for d in data_list if d['name'] not in removal_list]


def check_prereq_value(
    prereq_list: list[PrereqRow],
    datalist: list[DefaultDataRow],
) -> list[DefaultDataRow]:
    """Resolve prereq values from current data."""
    for item in datalist:
        for prereq in prereq_list:
            if prereq['prereq'] == item['name']:
                prereq['value'] = item['value']
    return datalist


def eliminate_no_prereq(datalist: list[DefaultDataRow]) -> list[DefaultDataRow]:
    """Filter items with unmet prerequisites.

    Runs to a fixed point. A single pass keeps a child whenever its prereq parent
    is present and satisfied (true-valued or a top-level '.0' group). But a mid-chain
    parent can itself be removed because ITS parent is unsatisfied while its own value
    is still 'true'. A single pass evaluates the grandchild against that soon-to-be-
    removed parent and wrongly keeps it, leaving it orphaned. Repeating the pass
    against the shrinking set prunes those orphans too, so an item survives only with
    an unbroken chain of satisfied parents up to a root. For a config with no such
    orphans the first pass is already stable, so valid trees are unaffected.
    """
    newlist: list[DefaultDataRow] = []
    for child in datalist:
        add = True
        if child['prereq'] != '':
            add = False
            for parent in datalist:
                if parent['name'] in child['prereq'] and (parent['value'].lower() == 'true' or '.0' in parent['order']):
                    if parent['name'] == child['prereq'] or '.' in child['prereq']:
                        add = True
                        break
        if add:
            newlist.append(child)
    return newlist


def filter_rows(data_list: list[DefaultDataRow]) -> list[DefaultDataRow]:
    """Recursive prerequisite chain filtering."""
    valid: list[DefaultDataRow] = []
    def has_valid(item: DefaultDataRow) -> bool:
        if not item.get('prereq'):
            return True
        for row in data_list:
            if row['name'] == item['prereq'] and row['value'].lower() == 'true' and has_valid(row):
                return True
        return False
    for item in data_list:
        if has_valid(item):
            valid.append(item)
    return valid