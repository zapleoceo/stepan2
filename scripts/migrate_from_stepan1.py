"""One-shot migration: Stepan-1 → Stepan-2.

Reads chats + messages from Stepan-1 (postgresql://…/stepan) and inserts
into Stepan-2 (postgresql://…/stepan2) as lead + channel_thread + message.

Usage (on Hetzner, inside stepan2 network):
    python3 migrate_from_stepan1.py

Environment (both must be set):
    S1_DSN  - Stepan-1 postgres DSN  (plain psycopg, no asyncpg)
    S2_DSN  - Stepan-2 postgres DSN  (plain psycopg, no asyncpg)
    BRANCH_ID  - target branch id in Stepan-2 (default: 1)
    CHANNEL_ID - target channel id in Stepan-2 (default: 1)

Safe to re-run: skips existing external_thread_id in channel_thread and
external_id in message (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations

import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

try:
    import psycopg
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "psycopg[binary]", "-q"], check=True)
    import psycopg  # type: ignore[no-redef]

S1_ASYNC = os.environ.get(
    "S1_DSN",
    "postgresql://stepan:508d8a5977b1acf3e50dff33b8991117e4bba05cd86ed485@postgres:5432/stepan",
)
S2_ASYNC = os.environ.get(
    "S2_DSN",
    "postgresql://stepan2:d30b40c13f3e315eb3c6db4948b77e9f8797ca69c0ca710d@postgres:5432/stepan2",
)

# strip asyncpg+ prefix if present
def _dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://").replace("asyncpg+postgresql://", "postgresql://")

S1_DSN = _dsn(S1_ASYNC)
S2_DSN = _dsn(S2_ASYNC)

BRANCH_ID = int(os.environ.get("BRANCH_ID", "1"))
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "1"))

# Stage values that exist in Stepan-1 and map directly to Stepan-2
VALID_STAGES = frozenset({
    "new", "qualifying", "presenting", "objection",
    "ready", "handed_off", "dormant", "manager",
})


def main() -> None:
    log.info("Connecting to Stepan-1: %s", S1_DSN.split("@")[1])
    log.info("Connecting to Stepan-2: %s", S2_DSN.split("@")[1])

    with psycopg.connect(S1_DSN) as s1, psycopg.connect(S2_DSN) as s2:
        _migrate(s1, s2)


def _migrate(s1: "psycopg.Connection", s2: "psycopg.Connection") -> None:
    # ── load all chats from Stepan-1 ─────────────────────────────────────────
    log.info("Reading chats from Stepan-1...")
    chats = s1.execute("""
        SELECT id, ig_thread_id, username, full_name, stage, ready_subtype,
               product_slug, last_in_at, last_out_at, created_at
        FROM chats
        ORDER BY id
    """).fetchall()
    log.info("  %d chats found", len(chats))

    # ── insert leads + channel_threads ───────────────────────────────────────
    # map: s1_chat_id → s2_thread_id
    thread_map: dict[int, int] = {}

    with s2.cursor() as cur:
        # fetch already-migrated threads (safe re-run)
        existing = cur.execute(
            "SELECT external_thread_id, id FROM channel_thread WHERE channel_id = %s",
            (CHANNEL_ID,)
        ).fetchall()
        thread_map_by_ext = {row[0]: row[1] for row in existing}

        log.info("  %d threads already in Stepan-2, skipping those", len(thread_map_by_ext))

        inserted_leads = 0
        inserted_threads = 0

        for (chat_id, ig_thread_id, username, full_name, stage, ready_subtype,
             product_slug, last_in_at, last_out_at, created_at) in chats:

            if ig_thread_id in thread_map_by_ext:
                # already migrated — just record the mapping
                s1chat_to_thread = thread_map_by_ext[ig_thread_id]
                thread_map[chat_id] = s1chat_to_thread
                continue

            display_name = full_name or username or f"lead_{chat_id}"
            safe_stage = stage if stage in VALID_STAGES else "new"

            # create lead
            lead_id = cur.execute("""
                INSERT INTO lead (branch_id, display_name, stage, ready_subtype, created_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (BRANCH_ID, display_name, safe_stage, ready_subtype, created_at)).fetchone()[0]
            inserted_leads += 1

            # create channel_thread
            thread_id = cur.execute("""
                INSERT INTO channel_thread
                    (lead_id, channel_id, external_thread_id, product_slug,
                     last_in_at, last_out_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (lead_id, CHANNEL_ID, ig_thread_id, product_slug,
                  last_in_at, last_out_at, created_at)).fetchone()[0]
            inserted_threads += 1

            thread_map[chat_id] = thread_id
            thread_map_by_ext[ig_thread_id] = thread_id

        s2.commit()
        log.info("  ✓ inserted %d leads, %d threads", inserted_leads, inserted_threads)

    # build reverse: need thread_id for chat rows already in thread_map_by_ext too
    # Re-fetch all chats that we didn't create above to fill thread_map fully
    with s2.cursor() as cur:
        for (chat_id, ig_thread_id, *_rest) in chats:
            if chat_id not in thread_map and ig_thread_id in thread_map_by_ext:
                thread_map[chat_id] = thread_map_by_ext[ig_thread_id]

    # ── migrate messages ──────────────────────────────────────────────────────
    log.info("Reading messages from Stepan-1...")
    BATCH = 500
    offset = 0
    total_inserted = 0
    total_skipped = 0

    while True:
        rows = s1.execute("""
            SELECT id, chat_id, direction, ig_message_id, text, sent_by, occurred_at
            FROM messages
            ORDER BY id
            LIMIT %s OFFSET %s
        """, (BATCH, offset)).fetchall()

        if not rows:
            break

        with s2.cursor() as cur:
            for (msg_id, chat_id, direction, ig_message_id, text, sent_by, occurred_at) in rows:
                thread_id = thread_map.get(chat_id)
                if thread_id is None:
                    total_skipped += 1
                    continue

                # external_id: use ig_message_id if available, else synthetic
                ext_id = ig_message_id or f"s1_{msg_id}"

                # ON CONFLICT DO NOTHING handles re-runs
                result = cur.execute("""
                    INSERT INTO message
                        (branch_id, thread_id, channel_id, external_id,
                         direction, sent_by, text, occurred_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (channel_id, external_id) DO NOTHING
                    RETURNING id
                """, (BRANCH_ID, thread_id, CHANNEL_ID, ext_id,
                      direction, sent_by, text or "", occurred_at))

                if result.fetchone():
                    total_inserted += 1
                else:
                    total_skipped += 1

        s2.commit()
        offset += BATCH
        log.info("  processed %d messages (total so far: %d inserted, %d skipped)",
                 len(rows), total_inserted, total_skipped)

    log.info("✓ Messages: %d inserted, %d skipped (already existed or chat not found)",
             total_inserted, total_skipped)
    log.info("Migration complete.")


if __name__ == "__main__":
    main()
