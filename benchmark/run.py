#
# This source file is part of the Bramble open source project.
#
# Copyright (c) 2026 Jaldis B.V.
#
# Licensed under the MIT OR Apache-2.0 license (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Python-level performance benchmark for the request pipeline.

    python benchmark/run.py                 # everything
    python benchmark/run.py --only sweep    # one section
    python benchmark/run.py --posts 200     # a bigger payload

Complements `crates/bramble-core/benches/graphql.rs`, which times the Rust parser/validator in
isolation: this measures what a caller actually pays, end to end, through the Python executor.

The three sections answer different questions, and it is worth knowing which one a change should
move:

* **phases** splits a request into parse, validate and execute. Parse and validate are fixed costs
  per request and are where the Rust front end shows up; execute scales with the response.
* **sweep** grows the response and reports cost per resolved field. This is the number that decides
  behaviour on list-heavy endpoints, and the one most sensitive to executor regressions -- the
  per-field figure should stay roughly flat across payload sizes.
* **async** measures latency and saturated throughput with awaiting resolvers. Field cost matters
  here only once the event loop is busy enough to make CPU the binding constraint.

Deliberately dependency-free and comparative only against itself: record a baseline before a
change, run it again after, and diff. Absolute numbers are machine-specific and not meaningful
across hosts.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import statistics
import time
import timeit
from collections.abc import Awaitable, Callable
from typing import Any

from bramble._bramble import parse_query, validate_document

import bramble

# --- Domain -------------------------------------------------------------------------------------
# Plain records with no GraphQL awareness, so the fields below are ordinary attribute reads and the
# measurement reflects library machinery rather than resolver bodies.


@dataclasses.dataclass
class PostRecord:
    id: str
    title: str
    body: str
    author: "UserRecord | None" = None


@dataclasses.dataclass
class UserRecord:
    id: str
    name: str
    email: str
    posts: list[PostRecord] = dataclasses.field(default_factory=list)


def build_user(post_count: int) -> UserRecord:
    user = UserRecord(id="1", name="Ada Lovelace", email="ada@example.com")
    user.posts = [
        PostRecord(id=str(index), title=f"Post {index}", body="body text " * 10, author=user)
        for index in range(post_count)
    ]
    return user


USER = build_user(20)

#: Simulated datastore latency for the async section. The per-author fetch is the interesting one:
#: it is the classic N+1, so overlapping it collapses `post_count` sequential waits into one.
USER_LATENCY = 0.003
POSTS_LATENCY = 0.003
AUTHOR_LATENCY = 0.001


# --- Schemas ------------------------------------------------------------------------------------


@bramble.type
class Post:
    id: bramble.ID
    title: str
    body: str
    author: "User"


@bramble.type
class User:
    id: bramble.ID
    name: str
    email: str
    posts: list[Post]


@bramble.type
class Query:
    @bramble.field
    def user(id: bramble.ID) -> User:
        return USER


@bramble.type
class AsyncPost:
    id: bramble.ID
    title: str
    body: str

    @bramble.field
    async def author(parent: bramble.Parent[PostRecord]) -> "AsyncUser":
        await asyncio.sleep(AUTHOR_LATENCY)
        return parent.author


@bramble.type
class AsyncUser:
    id: bramble.ID
    name: str
    email: str

    @bramble.field
    async def posts(parent: bramble.Parent[UserRecord]) -> list[AsyncPost]:
        await asyncio.sleep(POSTS_LATENCY)
        return parent.posts


@bramble.type
class AsyncQuery:
    @bramble.field
    async def user(id: bramble.ID) -> AsyncUser:
        await asyncio.sleep(USER_LATENCY)
        return USER


SCHEMA = bramble.Schema(query=Query)
ASYNC_SCHEMA = bramble.Schema(query=AsyncQuery)

QUERY = """
query GetUser($id: ID!) {
    user(id: $id) {
        id
        name
        email
        posts {
            id
            title
            body
            author {
                id
                name
            }
        }
    }
}
"""

VARIABLES = {"id": "1"}

#: 4 fields on the user, plus 4 per post and 2 on each post's author.
FIELDS_PER_POST = 6
BASE_FIELDS = 4


# --- Harness ------------------------------------------------------------------------------------


def best_microseconds(function: Callable[[], object], repeats: int) -> float:
    """The fastest per-call time over `repeats` rounds.

    The minimum rather than the mean: every competing process on the machine can only ever make a
    round slower, so the fastest one is the least contaminated estimate of the work itself.
    """
    function()
    timer = timeit.Timer(function)
    loops, _ = timer.autorange()
    return min(timer.repeat(repeat=repeats, number=loops)) / loops * 1_000_000


def check(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("errors"):
        raise SystemExit(f"benchmark query failed: {result['errors']}")
    return result


def run_phases(repeats: int) -> None:
    print("phases -- one request, split by stage\n")
    document = parse_query(QUERY)
    check(SCHEMA.execute(QUERY, variable_values=VARIABLES))

    measurements = [
        ("parse", lambda: parse_query(QUERY)),
        ("validate", lambda: validate_document(document, SCHEMA._compiled)),
        ("execute", lambda: SCHEMA.execute(QUERY, document=document, variable_values=VARIABLES)),
        ("end to end", lambda: SCHEMA.execute(QUERY, variable_values=VARIABLES)),
    ]
    print(f"{'stage':<14}{'time':>12}")
    print("-" * 26)
    for name, call in measurements:
        print(f"{name:<14}{best_microseconds(call, repeats):>9.1f} us")
    print()


def run_sweep(post_counts: list[int], repeats: int) -> None:
    print("sweep -- cost as the response grows\n")
    print(f"{'posts':>7}{'fields':>9}{'time':>13}{'per field':>13}")
    print("-" * 42)
    for count in post_counts:
        USER.posts = build_user(count).posts
        check(SCHEMA.execute(QUERY, variable_values=VARIABLES))

        fields = BASE_FIELDS + count * FIELDS_PER_POST
        microseconds = best_microseconds(lambda: SCHEMA.execute(QUERY, variable_values=VARIABLES), repeats)
        print(f"{count:>7}{fields:>9}{microseconds:>10.1f} us{microseconds / fields:>10.2f} us")
    USER.posts = build_user(20).posts
    print()


async def _measure_throughput(request: Callable[[], Awaitable[None]], concurrency: int, count: int) -> float:
    semaphore = asyncio.Semaphore(concurrency)

    async def one() -> None:
        async with semaphore:
            await request()

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(count)))
    return count / (time.perf_counter() - started)


async def _run_async(post_count: int, requests: int, concurrency_levels: list[int]) -> None:
    USER.posts = build_user(post_count).posts

    async def request() -> None:
        check(await ASYNC_SCHEMA.execute_async(QUERY, variable_values=VARIABLES))

    await request()

    ideal = (USER_LATENCY + POSTS_LATENCY + AUTHOR_LATENCY) * 1000
    serial = (USER_LATENCY + POSTS_LATENCY + post_count * AUTHOR_LATENCY) * 1000
    print(f"async -- awaiting resolvers, {post_count} posts")
    print(f"ideal latency {ideal:.1f} ms if every author fetch overlaps, {serial:.1f} ms if none do\n")

    timings = []
    for _ in range(requests):
        started = time.perf_counter()
        await request()
        timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    print(f"latency p50 {statistics.median(timings):.2f} ms | p95 {timings[int(len(timings) * 0.95)]:.2f} ms\n")

    print(f"{'concurrency':<14}{'throughput':>14}")
    print("-" * 28)
    for concurrency in concurrency_levels:
        rate = await _measure_throughput(request, concurrency, requests)
        print(f"{concurrency:<14}{rate:>9.0f} req/s")
    USER.posts = build_user(20).posts
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--only",
        choices=["phases", "sweep", "async"],
        action="append",
        help="run only these sections (repeatable); default runs all three",
    )
    parser.add_argument("--posts", type=int, default=100, help="payload size for the phases and async sections")
    parser.add_argument("--repeats", type=int, default=7, help="timing rounds per measurement")
    parser.add_argument("--requests", type=int, default=120, help="requests per async measurement")
    arguments = parser.parse_args()

    sections = arguments.only or ["phases", "sweep", "async"]
    USER.posts = build_user(arguments.posts).posts

    if "phases" in sections:
        run_phases(arguments.repeats)
    if "sweep" in sections:
        run_sweep([0, 1, 5, 20, 100, 400], arguments.repeats)
    if "async" in sections:
        asyncio.run(_run_async(arguments.posts, arguments.requests, [1, 16, 64]))


if __name__ == "__main__":
    main()
