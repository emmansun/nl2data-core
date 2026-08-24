"""MongoDB controlled fixture profile: deterministic in-memory collections.

The profile provisions the shared logical seed into the fake executor, so
conformance and equivalence cases run without any driver or service.  The
optional real-MongoDB integration profile lives in the integration tests
instead of this module, keeping the base package driver-free.
"""

from __future__ import annotations

from nl2data_core.adapters.mongodb.fake import FakeMongoExecutor
from nl2data_core.fixtures.base import FixtureProfile
from nl2data_core.fixtures.data import (
    EXPECTED_COUNTS,
    MONGO_FIXTURE_SETUP_FINGERPRINT,
    MONGO_RESULT_ASSERTIONS,
    MONGO_SEED,
    MongoResultAssertion,
)
from nl2data_core.fixtures.models import (
    FixtureSpec,
    FixtureVerificationError,
)


class MongoFixtureProfile(FixtureProfile):
    """In-memory MongoDB profile backed by the deterministic fake executor.

    The fake executor owns a copy of the seed documents, so provisioning,
    reset, and disposal never touch a native client or service.
    """

    def __init__(
        self,
        *,
        expected_setup_fingerprint: str = MONGO_FIXTURE_SETUP_FINGERPRINT,
    ) -> None:
        self._spec = FixtureSpec(
            fixture_id="sales-orders-mongo-v1",
            dialect="mongo",
            reset_strategy="recreate",
            expected_counts=EXPECTED_COUNTS,
        )
        self._expected_setup_fingerprint = expected_setup_fingerprint
        self._executor: FakeMongoExecutor | None = None

    @property
    def spec(self) -> FixtureSpec:
        """The versioned fixture spec this profile provisions."""
        return self._spec

    @property
    def executor(self) -> FakeMongoExecutor:
        """The provisioned fake executor (adapter-internal boundary)."""
        if self._executor is None:
            raise FixtureVerificationError("mongo fixture is not provisioned")
        return self._executor

    @property
    def result_assertions(self) -> tuple[MongoResultAssertion, ...]:
        """The shared Mongo result assertions bound to this profile."""
        return MONGO_RESULT_ASSERTIONS

    def provision(self) -> None:
        """Create the fixture to its declared seed state."""
        self._executor = FakeMongoExecutor(MONGO_SEED)

    def reset(self) -> None:
        """Restore the fixture to its seed state."""
        self.dispose()
        self.provision()

    def dispose(self) -> None:
        """Release the fake executor; safe to call more than once."""
        if self._executor is not None:
            self._executor.close()
            self._executor = None

    def verify(self) -> None:
        """Verify expected document counts; raise on any mismatch."""
        if self._executor is None:
            raise FixtureVerificationError("mongo fixture is not provisioned")
        for table_count in EXPECTED_COUNTS:
            count = self._executor.count_documents(
                collection=table_count.table, filter_={}
            )
            if count != table_count.count:
                raise FixtureVerificationError(
                    f"collection '{table_count.table}' has {count} documents, "
                    f"expected {table_count.count}"
                )
