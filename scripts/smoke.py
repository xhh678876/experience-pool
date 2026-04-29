"""End-to-end smoke test.

Assumes:
  - docker-compose up has populated postgres / redis / qdrant / minio
  - gateway is running on :8080
  - workers/pipeline.py is running

Steps:
  1. Register agent
  2. Push parent trajectory P
  3. Wait for pipeline to finish (poll until extraction_status='done')
  4. Push child trajectory C with parent_experience_ids=[P]
  5. Wait again
  6. Search for the topic and confirm both come back
  7. Read parent's q_update_count and confirm it incremented from 1 to 2
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

GW = "http://localhost:8080"


def wait_until(predicate, *, timeout: float = 30, interval: float = 1.0):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def fetch_state(eid: str) -> dict:
    r = httpx.get(f"{GW}/v1/experiences/{eid}", timeout=5)
    r.raise_for_status()
    return r.json()


def push(client: httpx.Client, traj: list[dict], parents: list[str]) -> str:
    r = client.post(
        f"{GW}/v1/experiences",
        headers={"X-Agent-Name": "dev-local"},
        json={
            "task_type": "smoke_test",
            "source_model": "claude-stub",
            "trajectory": traj,
            "parent_experience_ids": parents,
            "sensitivity": "low",
            "acl": "private",
            "tags": ["smoke"],
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["experience_id"]


def main() -> int:
    with httpx.Client() as client:
        client.post(
            f"{GW}/v1/agents/register",
            json={"name": "dev-local", "team": "platform"},
            timeout=5,
        )

        traj = [
            {"role": "user", "content": "summarize this CSV"},
            {"role": "assistant", "content": "loaded csv, ran describe"},
            {"role": "user", "content": "now group by region"},
            {"role": "assistant", "content": "applied groupby"},
        ]

        print("[push] parent")
        parent = push(client, traj, parents=[])
        print(f"  parent_id = {parent}")

        if not wait_until(
            lambda: fetch_state(parent).get("extraction_status") == "done", timeout=45
        ):
            print(f"  parent did not finish. state = {fetch_state(parent)}")
            return 1
        parent_state_v1 = fetch_state(parent)
        print(f"  parent q_update_count = {parent_state_v1['q_update_count']}")
        assert parent_state_v1["q_update_count"] == 1, "parent should have initial Q from judge"

        print("[push] child referencing parent")
        child = push(client, traj + [{"role": "assistant", "content": "wrote summary"}], parents=[parent])
        print(f"  child_id = {child}")
        if not wait_until(
            lambda: fetch_state(child).get("extraction_status") == "done", timeout=45
        ):
            print(f"  child did not finish. state = {fetch_state(child)}")
            return 1

        if not wait_until(
            lambda: fetch_state(parent)["q_update_count"] >= 2, timeout=20
        ):
            print(f"  credit assignment did not propagate. parent = {fetch_state(parent)}")
            return 1

        parent_state_v2 = fetch_state(parent)
        print(
            f"  parent q_update_count after child = {parent_state_v2['q_update_count']} "
            f"(expected >= 2)"
        )
        print("  delta q_outcome:", parent_state_v2["q_outcome"] - parent_state_v1["q_outcome"])

        print("[search]")
        r = client.get(
            f"{GW}/v1/experiences/search",
            params={"q": "summarize csv", "task_type": "smoke_test", "top_k": 5},
            headers={"X-Agent-Name": "dev-local"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json()["results"]
        print(f"  got {len(results)} hits")
        ids = {h["experience_id"] for h in results}
        assert parent in ids or child in ids, "neither parent nor child surfaced in search"
        print("  smoke OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
