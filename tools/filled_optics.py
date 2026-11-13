"""Development-only optical compensation for filled SVGs before native rendering."""
from copy import deepcopy
import re


BODY_ELEMENTS = {
    'headset': (), 'mouse': (0,), 'keyboard': (8,), 'microphone': (2,),
    'gamepad': (4,), 'speaker': (0,), 'webcam': (0,), 'health': (0,),
    'smart_home': (1,), 'watch': (1, 2, 3), 'display': (1,),
}


def profile(size):
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError('physical icon size must be a positive integer')
    growth = 0.10 * min(1.0, (16.0 / size) ** 1.25)
    return {'body_boundary': 'source-centreline', 'detail_growth_logical': growth,
            'centre_growth_logical': growth * 0.65,
            'headset_cups': 'previous-filled-contour'}


def number(value):
    return format(value, '.10g')


def compensate(svg, kind, size):
    """Keep open strokes; end large filled faces at their source centrelines.

    Growth is measured per edge in logical pixels. Ring-shaped cutouts expand
    outward while their white centre grows independently, rather than shrinking.
    """
    result = deepcopy(svg)
    if kind not in BODY_ELEMENTS:
        return result
    if result.attrib.get('data-filled-optical-size') == str(size):
        return result
    p = profile(size)
    group = next(n for n in list(result) if n.tag.rsplit('}', 1)[-1] == 'g')
    match = re.search(r'scale\(([^)]+)\)', group.attrib['transform'])
    scale = float(match.group(1))
    source = next(n for n in list(group) if n.tag.rsplit('}', 1)[-1] == 'svg')
    width = float(source.attrib.get('stroke-width', '2'))
    growth = p['detail_growth_logical'] / scale
    centre_growth = p['centre_growth_logical'] / scale

    for node in list(source.iter()):
        if kind == 'headset' and node.get('data-fill-existing-face') == 'earcup':
            # The user retained the previous filled cups. Only the band's
            # original width is restored; cups keep their former 1.6-unit rim.
            node.set('stroke', 'white')
            node.set('stroke-width', number(width * 0.8))
            continue
        index = node.attrib.get('data-source-index')
        if index is None and node.attrib.get('data-restore-body-outline') != 'true':
            continue
        index = int(index) if index is not None else None
        if kind == 'headset' and index == 0:
            # The source compound combines the band with both cup outlines.
            # Keep its exact band segments at the original stroke width; the
            # existing closed cup faces retain their previous filled contour.
            assert node.attrib['d'] == ('M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2'
                                        'v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2'
                                        'v-3a2 2 0 0 1 2-2h3')
            node.set('d', 'M3 14v-2a9 9 0 0 1 18 0v2')
            node.set('fill', 'none')
        elif index in BODY_ELEMENTS[kind]:
            # A fill plus its original stroke reaches half a stroke too far.
            # Removing that stroke puts the boundary exactly halfway between
            # the original visible outer and inner edges, without rescaling.
            node.set('stroke', 'none')
            if kind == 'smart_home':
                old = 'l7-6a2 2 0 0 1 2.582 0l7 6'
                new = 'L12 2 20.291 8.472'
                path = node.attrib['d']
                assert old in path or new in path
                node.set('d', path.replace(old, new))
                node.set('stroke-linejoin', 'miter')
                node.set('stroke-miterlimit', '2')
        elif node.attrib.get('data-restore-body-outline') == 'true':
            # This helper is clipped below: only its inward half may protect
            # the floor under the door. It must never enlarge the silhouette.
            node.set('stroke-width', number(width))
        elif node.attrib.get('stroke') == 'black':
            if node.tag.rsplit('}', 1)[-1] == 'circle':
                radius = float(node.attrib['r'])
                # R_out grows by growth, R_in grows by centre_growth.
                node.set('r', number(radius + (growth + centre_growth) / 2))
                node.set('stroke-width', number(width + growth - centre_growth))
            elif node.attrib.get('fill') == 'black':
                node.set('stroke-width', number(2 * growth))
            else:
                node.set('stroke-width', number(width + 2 * growth))
        elif node.attrib.get('stroke') == 'none' and node.attrib.get('fill') == 'black':
            node.set('stroke', 'black')
            node.set('stroke-width', number(2 * growth))
        elif kind == 'speaker' and index == 3:
            node.set('stroke-width', number(width + 2 * centre_growth))
    if kind == 'smart_home':
        import xml.etree.ElementTree as ET
        defs = next(n for n in list(source) if n.tag.rsplit('}', 1)[-1] == 'defs')
        mask = next(n for n in list(defs) if n.tag.rsplit('}', 1)[-1] == 'mask')
        body = next(n for n in mask if n.get('data-source-index') == '1')
        helper = next(n for n in mask if n.get('data-restore-body-outline') == 'true')
        clip_id = mask.attrib['id'] + '-body-centreline'
        prefix = source.tag.rsplit('}', 1)[0] + '}' if '}' in source.tag else ''
        clip = ET.SubElement(defs, prefix + 'clipPath', {'id': clip_id, 'clipPathUnits': 'userSpaceOnUse'})
        silhouette = deepcopy(body)
        silhouette.attrib.pop('data-source-index', None)
        clip.append(silhouette)
        helper.set('d', body.attrib['d'])
        helper.set('clip-path', 'url(#' + clip_id + ')')
    result.set('data-filled-optical-size', str(size))
    return result
