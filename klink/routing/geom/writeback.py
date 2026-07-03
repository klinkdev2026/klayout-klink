"""Write planned routes back to KLayout through primitive shape RPCs."""

from __future__ import annotations

from klink.routing.geom.geometry import parse_layer


def clear_route_layer(client, cell: str, *, route_layer: str = "10/0") -> dict:
    layer, datatype = parse_layer(route_layer)
    client.layer_ensure(layer, datatype, name="KLINK_ROUTES")
    return client.shape_delete(cell, layers=[route_layer], kinds=["paths"], limit=10000)


def commit_routes(
    client,
    cell: str,
    routes: list[dict],
    *,
    route_layer: str = "10/0",
    clear: bool = True,
) -> dict:
    layer, datatype = parse_layer(route_layer)
    client.layer_ensure(layer, datatype, name="KLINK_ROUTES")
    deleted = 0
    if clear:
        deleted = int(clear_route_layer(client, cell, route_layer=route_layer).get("deleted", 0))
    inserted = 0
    for route in routes:
        points = route.get("points_um") or []
        if len(points) < 2:
            continue
        width = float(route.get("width_um", 1.0))
        client.shape_insert_path(
            cell,
            layer=layer,
            datatype=datatype,
            points_um=points,
            width_um=width,
            begin_ext_um=width / 2.0,
            end_ext_um=width / 2.0,
            round_ends=False,
        )
        inserted += 1
    return {"cell": cell, "route_layer": route_layer, "deleted": deleted, "inserted": inserted}
