"""XmlStore — XML file I/O, parsing, and tree management.

Handles loading defaults.xml and userconfig_v2.xml, retrying on parse errors
for multi-instance file locking, and writing back consolidated userconfig.
"""
import logging
import time
import xml.etree.ElementTree as ET
from typing import Optional


class XmlStore:
    """Manages parsed XML trees for defaults and userconfig."""

    def __init__(self, device: str, userconfig_path: str, defaults_path: str) -> None:
        self.device = device
        self.userconfig_path = userconfig_path
        self.defaults_path = defaults_path
        self._user_tree: Optional[ET.ElementTree] = None
        self._user_root: Optional[ET.Element] = None
        self._defaults_root: Optional[ET.Element] = None

    @property
    def user_tree(self) -> Optional[ET.ElementTree]:
        return self._user_tree

    @property
    def user_root(self) -> Optional[ET.Element]:
        return self._user_root

    @property
    def defaults_root(self) -> Optional[ET.Element]:
        return self._defaults_root

    # Backward-compatible aliases for legacy xmlutils module
    auto_user_root = user_root
    auto_user_tree = user_tree
    auto_defaults_root = defaults_root

    def update_roots(self) -> None:
        """Re-parse both XML files into in-memory trees."""
        self._user_tree = try_parse(self.userconfig_path)
        self._user_root = self._user_tree.getroot() if self._user_tree is not None else None
        defaults_tree = try_parse(self.defaults_path)
        self._defaults_root = defaults_tree.getroot() if defaults_tree is not None else None

    def write_userconfig(self, tree: Optional[ET.ElementTree] = None) -> None:
        """Write userconfig after consolidation and dedup."""
        if tree is None:
            tree = self._user_tree
        consolidate_sort_and_write(tree, self.userconfig_path)

    def really_write_userconfig(self, tree: Optional[ET.ElementTree] = None) -> None:
        """Direct write without consolidation."""
        if tree is None:
            tree = self._user_tree
        if tree is None:
            return
        root = tree.getroot()
        assert root is not None
        ET.indent(root, " ")
        tree.write(self.userconfig_path, "utf-8")

    def consolidated_tree(self, tree: Optional[ET.ElementTree] = None) -> Optional[ET.ElementTree]:
        """Return a deduplicated/sorted copy of the tree without writing."""
        if tree is None:
            tree = self._user_tree
        if tree is None:
            return None
        return consolidate_sort_and_write(tree, ret=True)


def try_parse(file_path: str, max_attempts: int = 3, delay: float = 0.1) -> Optional[ET.ElementTree]:
    """Parse XML with retry on ParseError (multi-instance file locking)."""
    attempt = 0
    while attempt < max_attempts:
        try:
            return ET.parse(file_path)
        except ET.ParseError:
            attempt += 1
            time.sleep(delay)
    logging.error("All %d attempts to parse %s failed.", max_attempts, file_path)
    return None


def consolidate_sort_and_write(
    tree: Optional[ET.ElementTree],
    path: Optional[str] = None,
    ret: bool = False,
) -> Optional[ET.ElementTree]:
    """Deduplicate and reorder all entry types in userconfig.

    - Ensures one <profileMappings> per (sim, cls, model)
    - Ensures <models> entries are byte-unique
    - Sorts each section by its natural key
    """
    if tree is None:
        return None
    root = tree.getroot()
    if root is None:
        return None

    # Deduplicate profileMappings (last wins per key)
    profile_map: dict[tuple[str, str, str], bytes] = {}
    for elem in root.findall('profileMappings'):
        key = (elem.findtext('sim') or '', elem.findtext('cls') or '', elem.findtext('model') or '')
        profile_map[key] = ET.tostring(elem)

    # Deduplicate models (byte-unique)
    seen: set[bytes] = set()
    models_list: list[ET.Element] = []
    for elem in root.findall('models'):
        raw = ET.tostring(elem)
        if raw not in seen:
            seen.add(raw)
            models_list.append(elem)

    sim_list: list[ET.Element] = root.findall('simSettings')
    class_list: list[ET.Element] = root.findall('classSettings')
    sc_list: list[ET.Element] = root.findall('sc_overrides')

    # Clear old entries
    for child in list(root):
        if child.tag in ('profileMappings', 'models', 'simSettings', 'classSettings', 'sc_overrides'):
            root.remove(child)

    # Reinsert sorted
    _reinsert(root, sim_list, ('sim', 'device', 'name'))
    _reinsert(root, class_list, ('sim', 'type', 'device', 'name'))
    _reinsert(root, sc_list, ('model', 'name'))

    def profile_key(raw_bytes: bytes) -> tuple[str, str, str, str]:
        e = ET.fromstring(raw_bytes)
        return (e.findtext('sim') or '', e.findtext('cls') or '',
                e.findtext('model') or '', e.findtext('active_profile') or '')
    for raw in sorted(profile_map.values(), key=profile_key):
        root.append(ET.fromstring(raw))

    _reinsert(root, models_list, ('sim', 'model', 'profile', 'device', 'name'))

    if ret:
        return tree
    ET.indent(root, " ")
    if path is not None:
        tree.write(path, "utf-8")
    return None


def _reinsert(root: ET.Element, elements: list[ET.Element], key_fields: tuple[str, ...]) -> None:
    """Sort elements by key fields and reinsert into root."""
    def key(e: ET.Element) -> tuple[str, ...]:
        return tuple(e.findtext(f, '') for f in key_fields)
    for e in sorted(elements, key=key):
        root.append(e)