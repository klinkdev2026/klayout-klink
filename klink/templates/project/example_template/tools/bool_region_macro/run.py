import pya

def selectedShapes(view):
    return sorted([obj for obj in view.each_object_selected()], key=lambda s: s.seq)

def main():
    view = pya.Application.instance().main_window().current_view()
    try:
        cellview = view.active_cellview()
        if not cellview.is_valid():
            raise ValueError("当前没有打开的版图单元！")
    except RuntimeError:
        raise ValueError("当前没有打开的版图单元！")
    cell = cellview.cell
    layout = cellview.layout()
    dbu = layout.dbu
    selected_shapes = selectedShapes(view)
    if not selected_shapes:
        raise ValueError("未选中任何对象！请先选中要进行运算的区域。")
    # ==================== 配置区域 ====================
    layer1 = layout.layer(1, 0)      # 第一个图层 (1/0)
    layer2 = layout.layer(100, 0)    # 第二个图层 (100/0)
    output_layer = layout.layer(3, 0)  # 输出结果的图层 (3/0)
    # =================================================
    selected_region = pya.Region()
    for obj in selected_shapes:
        if obj.is_cell_inst():
            inst = obj.inst()
            inst_cell = inst.cell
            trans = obj.trans()
            inst_region1 = pya.Region(inst_cell.begin_shapes_rec(layer1))
            if not inst_region1.is_empty():
                inst_region1.transform(trans)
                selected_region.insert(inst_region1)
            inst_region2 = pya.Region(inst_cell.begin_shapes_rec(layer2))
            if not inst_region2.is_empty():
                inst_region2.transform(trans)
                selected_region.insert(inst_region2)
        else:
            shape = obj.shape
            trans = obj.trans()
            if shape.is_polygon():
                selected_region.insert(shape.polygon.transformed(trans))
            elif shape.is_box():
                selected_region.insert(shape.box.transformed(trans))
            elif shape.is_path():
                selected_region.insert(shape.path.transformed(trans).polygon())
            else:
                selected_region.insert(shape.bbox().transformed(trans))
    if selected_region.is_empty():
        raise ValueError("选中的区域不包含有效图形！")
    region_layer1 = pya.Region(cell.begin_shapes_rec(layer1))
    region_layer2 = pya.Region(cell.begin_shapes_rec(layer2))
    region_layer1 = region_layer1 & selected_region
    region_layer2 = region_layer2 & selected_region
    result = region_layer1 & region_layer2
    cell.shapes(output_layer).insert(result)
    print(f"完成！结果已输出到图层 {output_layer}。")
    print(f"运算结果：{result.count()} 个图形，总面积 {result.area()} (数据库单位平方)")

if __name__ == "__main__":
    main()
