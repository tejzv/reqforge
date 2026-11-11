from __future__ import annotations

import unittest

from reqforge.infer import cluster_and_infer_endpoints, infer_schema_from_samples
from reqforge.models import CapturedRequest


def _req(
    req_id: str,
    method: str,
    path: str,
    *,
    query: list[tuple[str, str]] | None = None,
    body_json: object = None,
    resp_json: object = None,
) -> CapturedRequest:
    return CapturedRequest(
        id=req_id,
        method=method,
        url=f"https://api.example.com{path}",
        scheme="https",
        host="api.example.com",
        port=None,
        path=path,
        query_params=query or [],
        body_kind="json" if body_json is not None else "none",
        body_json=body_json,
        response_status=200,
        response_json=resp_json,
    )


class TestInference(unittest.TestCase):
    def test_single_and_multi_request_path_parameterization(self) -> None:
        r1 = _req("1", "GET", "/api/v1/users/alice/orders/550e8400-e29b-41d4-a716-446655440000", query=[("limit", "10"), ("utm_source", "ads")], resp_json=[{"id": 1, "total": 10}])
        r2 = _req("2", "GET", "/api/v1/users/bob/orders/660e8400-e29b-41d4-a716-446655440001", query=[("limit", "25"), ("include_items", "true")], resp_json=[{"id": 2, "total": 12.5, "note": None}])
        r3 = _req("3", "GET", "/api/v2/users/42", resp_json={"id": 42, "name": "Tej"})

        endpoints = cluster_and_infer_endpoints([r1, r2, r3])
        self.assertEqual(len(endpoints), 2)

        ep_orders = endpoints[0]
        self.assertEqual(ep_orders.path_template, "/api/v1/users/{user_id}/orders/{order_id}")
        self.assertEqual([p.name for p in ep_orders.path_params], ["user_id", "order_id"])
        self.assertEqual(ep_orders.path_params[1].inferred_type, "uuid")

        q_map = {q.wire_name: q for q in ep_orders.query_params}
        self.assertNotIn("utm_source", q_map)
        self.assertEqual(q_map["limit"].inferred_type, "int")
        self.assertTrue(q_map["limit"].required)
        self.assertEqual(q_map["include_items"].inferred_type, "bool")
        self.assertFalse(q_map["include_items"].required)

        ep_v2 = endpoints[1]
        self.assertEqual(ep_v2.path_template, "/api/v2/users/{user_id}")
        self.assertEqual(ep_v2.path_params[0].inferred_type, "int")
        self.assertEqual(ep_v2.operation_id, "get_user")

    def test_graphql_operation_splitting(self) -> None:
        g1 = _req("1", "POST", "/graphql", body_json={"operationName": "GetViewer", "variables": {}}, resp_json={"data": {"id": "1"}})
        g2 = _req("2", "POST", "/graphql", body_json={"operationName": "CreatePost", "variables": {"title": "Hi"}}, resp_json={"data": {"ok": True}})
        endpoints = cluster_and_infer_endpoints([g1, g2])
        self.assertEqual(len(endpoints), 2)
        self.assertEqual([e.operation_id for e in endpoints], ["graphql_get_viewer", "graphql_create_post"])

    def test_schema_merging_nullables_optional_and_numeric_widening(self) -> None:
        samples = [
            {"id": 1, "score": 10, "from": "web", "tags": ["a"], "meta": {}},
            {"id": 2, "score": 10.5, "from": None, "extra": "ok", "tags": ["b"], "meta": {}},
        ]
        node = infer_schema_from_samples(samples, root_name="ItemResponse")
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.model_name, "ItemResponse")
        self.assertEqual(node.fields["score"].node.kind, "number")
        self.assertTrue(node.fields["from"].nullable)
        self.assertEqual(node.fields["from"].name, "from_")
        self.assertFalse(node.fields["extra"].required)
        self.assertIsNone(node.fields["meta"].node.model_name)
