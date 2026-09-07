"""Explicit relationships scoped to an authoring model, never category guesses."""
from ..model import ElementRef


def same_source(a: ElementRef, b: ElementRef) -> bool:
    if a.model_index >= 0 and b.model_index >= 0:
        if a.model_index != b.model_index:
            return False
        return not (a.source_file and b.source_file) or a.source_file.casefold() == b.source_file.casefold()
    return bool(a.source_file and b.source_file and a.source_file.casefold() == b.source_file.casefold())


def hosted_by(child: ElementRef, host: ElementRef) -> bool:
    """A namespaced path or authoring Host Id must point to THIS host."""
    path = child.prop('NC:HostPath')
    if path:
        return path == host.path_id
    host_id = child.prop('Host Id', 'Host Element Id', 'Id de anfitrión')
    element_id = host.prop('Element Id', 'Id de elemento')
    return bool(host_id and element_id and same_source(child, host) and
                host_id.strip() == element_id.strip())


def designed_contact(a: ElementRef, b: ElementRef) -> bool:
    """An explicit per-element reference; a global yes/no flag is insufficient."""
    return bool(b.path_id and a.prop('NC:DesignedContactWith') == b.path_id) or bool(a.path_id and b.prop('NC:DesignedContactWith') == a.path_id)
