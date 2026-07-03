"""klink recording (pya).
72 action(s) over 47.7s, 8 event(s) observed.
Started from a layout with 1 cell(s), tops=['TOP'], active='TOP'.

Dual-mode:
  python <this-file>.py      -> replays via klink pyexec
  KLayout Macro IDE          -> runs bare pya directly
"""

def _C(ly, name):
    c = ly.cell(name)
    return c if c is not None else ly.create_cell(name)

def _LI(ly, L, D):
    li = ly.find_layer(pya.LayerInfo(L, D))
    return li if li is not None else ly.insert_layer(pya.LayerInfo(L, D))

def _bootstrap(ly, pya):
    for nm in ['TOP']:
        _C(ly, nm)
    for _L, _D, _name in [(999, 1, 'KLINK_ANCHORS'), (999, 99, None)]:
        _LI(ly, _L, _D)


def _replay(ly, pya):
    # -- manual --
    _LI(ly, 1, 0)  # + 21.70s
    _LI(ly, 900, 0)  # + 21.70s
    _LI(ly, 997, 99)  # + 21.70s
    _LI(ly, 998, 99)  # + 21.70s
    _C(ly, 'ANCHOR_01_STRAIGHT')  # + 21.70s
    _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    _c = ly.cell('TOP')  # + 21.70s
    if _c is not None:
        _c.name = 'Port$6'
    cell = _C(ly, 'ANCHOR_01_STRAIGHT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(0, 0, 20000, 10000))
    cell = _C(ly, 'ANCHOR_01_STRAIGHT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(100000, 0, 120000, 10000))
    cell = _C(ly, 'ANCHOR_01_STRAIGHT')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('01_STRAIGHT: two facing ports, no anchors/obstacles', pya.Trans(0, 24000)))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(0, 0, 18000, 10000))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(100000, 0, 118000, 10000))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('02_WAYPOINT: route A -> WP1 -> B', pya.Trans(0, 64000)))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('WP1 must_pass', pya.Trans(64500, 44500)))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Polygon([pya.Point(60000, 37000), pya.Point(57000, 40000), pya.Point(60000, 43000), pya.Point(63000, 40000)]))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(55200, 40000), pya.Point(64800, 40000)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(60000, 35200), pya.Point(60000, 44800)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(20000, 20000, 140000, 40000))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(150000, 66000, 170000, 78000))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('03_EDGE_SLIDE: port may slide along the upper device edge', pya.Trans(0, 82000)))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('EDGE_SLIDE upper_edge', pya.Trans(84500, 50500)))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Polygon([pya.Point(80000, 43000), pya.Point(77000, 46000), pya.Point(80000, 49000), pya.Point(83000, 46000)]))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(35000, 40000), pya.Point(125000, 40000)], 700, 350, 350, False))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(75200, 46000), pya.Point(84800, 46000)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(80000, 41200), pya.Point(80000, 50800)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(0, 0, 18000, 10000))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(120000, 0, 138000, 10000))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('04_OBSTACLE: route must avoid keepout on 900/0', pya.Trans(0, 54000)))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('KEEP_OUT', pya.Trans(55000, 31000)))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('optional_above', pya.Trans(73500, 46500)))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Polygon([pya.Point(69000, 39000), pya.Point(66000, 42000), pya.Point(69000, 45000), pya.Point(72000, 42000)]))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(64200, 42000), pya.Point(73800, 42000)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(69000, 37200), pya.Point(69000, 46800)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    li = _LI(ly, 900, 0)
    cell.shapes(li).insert(pya.Box(52000, -18000, 86000, 28000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(0, 7000, 14000, 13000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(0, 21000, 14000, 27000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(0, 35000, 14000, 41000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(0, 49000, 14000, 55000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(110000, -5000, 132000, 5000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(110000, 9000, 132000, 19000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(110000, 23000, 132000, 33000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(110000, 37000, 132000, 47000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(110000, 51000, 132000, 61000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 1, 0)
    cell.shapes(li).insert(pya.Box(110000, 65000, 132000, 75000))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('05_FANOUT: 4 internal demand ports choose 6 candidate pads', pya.Trans(-8000, 98000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 997, 99)
    cell.shapes(li).insert(pya.Text('ASSIGN min_crossing', pya.Trans(74500, 39500)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Polygon([pya.Point(70000, 32000), pya.Point(67000, 35000), pya.Point(70000, 38000), pya.Point(73000, 35000)]))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(65200, 35000), pya.Point(74800, 35000)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(70000, 30200), pya.Point(70000, 39800)], 350, 175, 175, False))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(42000, 4000), pya.Point(42000, 76000)], 400, 200, 200, False))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    li = _LI(ly, 998, 99)
    cell.shapes(li).insert(pya.Path([pya.Point(84000, 4000), pya.Point(84000, 76000)], 400, 200, 200, False))
    cell = _C(ly, 'ANCHOR_01_STRAIGHT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_straight', 'orientation': 0.0, 'port_name': 'A', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 20000, 5000)))
    cell = _C(ly, 'ANCHOR_01_STRAIGHT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_straight', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 100000, 5000)))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_waypoint', 'orientation': 0.0, 'port_name': 'A', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 18000, 5000)))
    cell = _C(ly, 'ANCHOR_02_WAYPOINT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_waypoint', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 100000, 5000)))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_slide', 'orientation': 90.0, 'port_name': 'A_EDGE', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 58000, 43000)))
    cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_slide', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 150000, 72000)))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_obstacle', 'orientation': 0.0, 'port_name': 'A', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 18000, 5000)))
    cell = _C(ly, 'ANCHOR_04_OBSTACLE')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_obstacle', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 120000, 5000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig0', 'orientation': 0.0, 'port_name': 'IN0', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 10000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig1', 'orientation': 0.0, 'port_name': 'IN1', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 24000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig2', 'orientation': 0.0, 'port_name': 'IN2', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 38000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig3', 'orientation': 0.0, 'port_name': 'IN3', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 52000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD0', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 0)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD1', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 14000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD2', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 28000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD3', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 42000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD4', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 56000)))
    cell = _C(ly, 'ANCHOR_05_FANOUT')  # + 21.70s
    variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD5', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})
    cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 70000)))


if __name__ == '__main__':
    import os, sys
    _IN_KLAYOUT = False
    try:
        import pya
        pya.Application.instance()  # must be real KLayout pya
        _IN_KLAYOUT = True
    except (ImportError, AttributeError):
        pass
    if not _IN_KLAYOUT:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from klink import KLinkClient
        _kl = KLinkClient().connect()
        _kl.pyexec(
        "import pya\n"
        "mw = pya.Application.instance().main_window()\n"
        "ly = mw.current_view().active_cellview().layout()\n"
        "\n"
        "def _C(ly, name):\n"
        "    c = ly.cell(name)\n"
        "    return c if c is not None else ly.create_cell(name)\n"
        "\n"
        "def _LI(ly, L, D):\n"
        "    li = ly.find_layer(pya.LayerInfo(L, D))\n"
        "    return li if li is not None else ly.insert_layer(pya.LayerInfo(L, D))\n"
        "\n"
        "_C(ly, 'TOP')\n"
        "_LI(ly, 999, 1)\n"
        "_LI(ly, 999, 99)\n"
        "\n"
        "_LI(ly, 1, 0)\n"
        "_LI(ly, 900, 0)\n"
        "_LI(ly, 997, 99)\n"
        "_LI(ly, 998, 99)\n"
        "_C(ly, 'ANCHOR_01_STRAIGHT')\n"
        "_C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "_C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "_C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "_C(ly, 'ANCHOR_05_FANOUT')\n"
        "_c = ly.cell('TOP')\n"
        "if _c is not None:\n"
        "    _c.name = 'Port$6'\n"
        "cell = _C(ly, 'ANCHOR_01_STRAIGHT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(0, 0, 20000, 10000))\n"
        "cell = _C(ly, 'ANCHOR_01_STRAIGHT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(100000, 0, 120000, 10000))\n"
        "cell = _C(ly, 'ANCHOR_01_STRAIGHT')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('01_STRAIGHT: two facing ports, no anchors/obstacles', pya.Trans(0, 24000)))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(0, 0, 18000, 10000))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(100000, 0, 118000, 10000))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('02_WAYPOINT: route A -> WP1 -> B', pya.Trans(0, 64000)))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('WP1 must_pass', pya.Trans(64500, 44500)))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Polygon([pya.Point(60000, 37000), pya.Point(57000, 40000), pya.Point(60000, 43000), pya.Point(63000, 40000)]))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(55200, 40000), pya.Point(64800, 40000)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(60000, 35200), pya.Point(60000, 44800)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(20000, 20000, 140000, 40000))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(150000, 66000, 170000, 78000))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('03_EDGE_SLIDE: port may slide along the upper device edge', pya.Trans(0, 82000)))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('EDGE_SLIDE upper_edge', pya.Trans(84500, 50500)))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Polygon([pya.Point(80000, 43000), pya.Point(77000, 46000), pya.Point(80000, 49000), pya.Point(83000, 46000)]))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(35000, 40000), pya.Point(125000, 40000)], 700, 350, 350, False))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(75200, 46000), pya.Point(84800, 46000)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(80000, 41200), pya.Point(80000, 50800)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(0, 0, 18000, 10000))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(120000, 0, 138000, 10000))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('04_OBSTACLE: route must avoid keepout on 900/0', pya.Trans(0, 54000)))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('KEEP_OUT', pya.Trans(55000, 31000)))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('optional_above', pya.Trans(73500, 46500)))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Polygon([pya.Point(69000, 39000), pya.Point(66000, 42000), pya.Point(69000, 45000), pya.Point(72000, 42000)]))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(64200, 42000), pya.Point(73800, 42000)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(69000, 37200), pya.Point(69000, 46800)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "li = _LI(ly, 900, 0)\n"
        "cell.shapes(li).insert(pya.Box(52000, -18000, 86000, 28000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(0, 7000, 14000, 13000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(0, 21000, 14000, 27000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(0, 35000, 14000, 41000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(0, 49000, 14000, 55000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(110000, -5000, 132000, 5000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(110000, 9000, 132000, 19000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(110000, 23000, 132000, 33000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(110000, 37000, 132000, 47000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(110000, 51000, 132000, 61000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 1, 0)\n"
        "cell.shapes(li).insert(pya.Box(110000, 65000, 132000, 75000))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('05_FANOUT: 4 internal demand ports choose 6 candidate pads', pya.Trans(-8000, 98000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 997, 99)\n"
        "cell.shapes(li).insert(pya.Text('ASSIGN min_crossing', pya.Trans(74500, 39500)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Polygon([pya.Point(70000, 32000), pya.Point(67000, 35000), pya.Point(70000, 38000), pya.Point(73000, 35000)]))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(65200, 35000), pya.Point(74800, 35000)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(70000, 30200), pya.Point(70000, 39800)], 350, 175, 175, False))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(42000, 4000), pya.Point(42000, 76000)], 400, 200, 200, False))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "li = _LI(ly, 998, 99)\n"
        "cell.shapes(li).insert(pya.Path([pya.Point(84000, 4000), pya.Point(84000, 76000)], 400, 200, 200, False))\n"
        "cell = _C(ly, 'ANCHOR_01_STRAIGHT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_straight', 'orientation': 0.0, 'port_name': 'A', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 20000, 5000)))\n"
        "cell = _C(ly, 'ANCHOR_01_STRAIGHT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_straight', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 100000, 5000)))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_waypoint', 'orientation': 0.0, 'port_name': 'A', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 18000, 5000)))\n"
        "cell = _C(ly, 'ANCHOR_02_WAYPOINT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_waypoint', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 100000, 5000)))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_slide', 'orientation': 90.0, 'port_name': 'A_EDGE', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 58000, 43000)))\n"
        "cell = _C(ly, 'ANCHOR_03_EDGE_SLIDE')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_slide', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 150000, 72000)))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_obstacle', 'orientation': 0.0, 'port_name': 'A', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 18000, 5000)))\n"
        "cell = _C(ly, 'ANCHOR_04_OBSTACLE')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'net_obstacle', 'orientation': 180.0, 'port_name': 'B', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 4.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 120000, 5000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig0', 'orientation': 0.0, 'port_name': 'IN0', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 10000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig1', 'orientation': 0.0, 'port_name': 'IN1', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 24000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig2', 'orientation': 0.0, 'port_name': 'IN2', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 38000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': 'sig3', 'orientation': 0.0, 'port_name': 'IN3', 'port_type': 'electrical', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 3.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 14000, 52000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD0', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 0)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD1', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 14000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD2', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 28000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD3', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 42000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD4', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 56000)))\n"
        "cell = _C(ly, 'ANCHOR_05_FANOUT')\n"
        "variant = ly.create_cell('Port', 'klink_port', {'access_mode': 'point', 'label': '', 'layer': pya.LayerInfo(999, 99), 'net': '', 'orientation': 180.0, 'port_name': 'PAD5', 'port_type': 'candidate_sink', 'show_label': True, 'slide_allowed': False, 'slide_edge': '', 'target_layer': '10/0', 'width_um': 5.0})\n"
        "cell.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(0, False, 110000, 70000)))\n"
        )
        _kl.close()
    else:
        mw = pya.Application.instance().main_window()
        if mw is not None:
            ly = mw.current_view().active_cellview().layout()
            _replay(ly, pya)
