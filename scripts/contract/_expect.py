"""Assertions shared by the contract rehearsal steps.

These files drive a real app over TestClient and then read the body. Reading
it WITHOUT checking the status is how a refusal turns into a puzzle: a
`GET /api/playbooks` that answered 401 returned `{"detail": ...}`, the caller
iterated it, and the run died on

    TypeError: string indices must be integers, not 'str'

roughly 180 lines into a heredoc, naming neither the request nor the status.
Two real refusals hid behind that shape — an auth default that differed
between CI and a developer box, and an auth default that MOVED between the
two core artifacts under test. Both are one-line diagnoses when the status is
checked at the door.

Import as `from _expect import ok` — each step runs with its own directory on
sys.path[0], because it is executed by path under the INSTALLED artifact's
interpreter, not imported from this repo.
"""


def ok(response, *, status: int = 200):
    """Return a response's JSON, or fail naming the request and the status.

    The message carries the method, the URL, the status and the start of the
    body, because the body is where the app says WHY it refused.
    """
    if response.status_code != status:
        raise AssertionError(
            f"{response.request.method} {response.request.url}"
            f" -> {response.status_code} (expected {status}): {response.text[:300]}"
        )
    return response.json()
