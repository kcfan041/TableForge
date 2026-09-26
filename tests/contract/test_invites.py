from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from tableforge.composition import Repositories
from tableforge.domain.auth import (
    AuthenticationError,
    InviteScope,
    OperatorRole,
)
from tableforge.domain.ids import NodeId

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DAY = timedelta(days=1)


def test_cluster_id_is_created_once_and_stable(repositories: Repositories) -> None:
    first = repositories.auth.cluster_id(NOW)

    assert repositories.auth.cluster_id(NOW + DAY) == first


def test_cluster_id_is_unique_under_concurrency(repositories: Repositories) -> None:
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = set(executor.map(lambda _: repositories.auth.cluster_id(NOW), range(16)))

    assert len(ids) == 1


def test_node_invite_is_single_use_and_yields_only_a_session(
    repositories: Repositories,
) -> None:
    auth = repositories.auth
    invite, secret = auth.create_invite(InviteScope(node=True, role=None), NOW, DAY, "tester")

    redemption = auth.redeem_invite(secret, NodeId("node-a"), NOW, DAY, DAY)

    assert redemption.session is not None and redemption.operator is None
    assert auth.authenticate(redemption.session.token, NOW) == NodeId("node-a")
    with pytest.raises(AuthenticationError, match="already used"):
        auth.redeem_invite(secret, NodeId("node-b"), NOW, DAY, DAY)
    with pytest.raises(AuthenticationError, match="already used"):
        auth.redeem_invite(secret, NodeId("node-a"), NOW, DAY, DAY)
    listed = {item.invite_id: item for item in auth.list_invites()}
    assert listed[invite.invite_id].redeemed_by == "node-a"


def test_secret_is_not_a_credential(repositories: Repositories) -> None:
    auth = repositories.auth
    _, secret = auth.create_invite(InviteScope(node=True, role=OperatorRole.OPERATOR), NOW, DAY)

    with pytest.raises(AuthenticationError):
        auth.authenticate(secret, NOW)
    with pytest.raises(AuthenticationError):
        auth.authenticate_operator(secret, NOW)


def test_combined_invite_issues_separate_credentials(repositories: Repositories) -> None:
    auth = repositories.auth
    _, secret = auth.create_invite(
        InviteScope(node=True, role=OperatorRole.OPERATOR), NOW, DAY, "tester"
    )

    redemption = auth.redeem_invite(secret, NodeId("node-a"), NOW, DAY, DAY)

    assert redemption.session is not None and redemption.operator is not None
    operator = auth.authenticate_operator(redemption.operator.token, NOW)
    assert operator.role is OperatorRole.OPERATOR
    assert operator.label == "tester"
    with pytest.raises(AuthenticationError, match="operator"):
        auth.authenticate(redemption.operator.token, NOW)
    with pytest.raises(AuthenticationError):
        auth.authenticate_operator(redemption.session.token, NOW)


def test_operator_only_invite_needs_no_node(repositories: Repositories) -> None:
    auth = repositories.auth
    _, secret = auth.create_invite(InviteScope(node=False, role=OperatorRole.VIEWER), NOW, DAY)

    redemption = auth.redeem_invite(secret, None, NOW, DAY, DAY)

    assert redemption.session is None
    assert redemption.operator is not None and redemption.operator.role is OperatorRole.VIEWER


def test_node_invite_requires_node_id(repositories: Repositories) -> None:
    auth = repositories.auth
    _, secret = auth.create_invite(InviteScope(node=True, role=None), NOW, DAY)

    with pytest.raises(AuthenticationError, match="node id"):
        auth.redeem_invite(secret, None, NOW, DAY, DAY)
    assert auth.redeem_invite(secret, NodeId("node-a"), NOW, DAY, DAY).session is not None


def test_expired_and_revoked_invites_are_rejected(repositories: Repositories) -> None:
    auth = repositories.auth
    _, expired = auth.create_invite(InviteScope(node=True, role=None), NOW, timedelta(hours=1))
    revoked_invite, revoked = auth.create_invite(InviteScope(node=True, role=None), NOW, DAY)

    assert auth.revoke_invite(revoked_invite.invite_id)
    assert not auth.revoke_invite(revoked_invite.invite_id)
    with pytest.raises(AuthenticationError, match="expired"):
        auth.redeem_invite(expired, NodeId("n"), NOW + timedelta(hours=1), DAY, DAY)
    with pytest.raises(AuthenticationError, match="revoked"):
        auth.redeem_invite(revoked, NodeId("n"), NOW, DAY, DAY)
    with pytest.raises(AuthenticationError, match="invalid"):
        auth.redeem_invite("not-a-secret", NodeId("n"), NOW, DAY, DAY)


def test_invite_redeems_once_under_concurrency(repositories: Repositories) -> None:
    auth = repositories.auth
    _, secret = auth.create_invite(InviteScope(node=True, role=None), NOW, DAY)

    def redeem(index: int) -> bool:
        try:
            auth.redeem_invite(secret, NodeId(f"racer-{index}"), NOW, DAY, DAY)
            return True
        except AuthenticationError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(redeem, range(12)))

    assert sum(results) == 1


def test_operator_credentials_expire_and_revoke(repositories: Repositories) -> None:
    auth = repositories.auth
    _, secret = auth.create_invite(InviteScope(node=False, role=OperatorRole.VIEWER), NOW, DAY)
    operator = auth.redeem_invite(secret, None, NOW, DAY, timedelta(days=2)).operator
    assert operator is not None

    assert auth.authenticate_operator(operator.token, NOW + DAY).role is OperatorRole.VIEWER
    with pytest.raises(AuthenticationError):
        auth.authenticate_operator(operator.token, NOW + timedelta(days=2))
    assert [item.operator_id for item in auth.list_operators()] == [operator.operator_id]
    assert auth.revoke_operator(operator.operator_id)
    with pytest.raises(AuthenticationError):
        auth.authenticate_operator(operator.token, NOW)
    assert auth.list_operators() == ()


def test_worker_slots_belong_to_their_node(repositories: Repositories) -> None:
    auth = repositories.auth
    first = auth.start_worker(NodeId("node-a"), "gpu0", NOW)
    second = auth.start_worker(NodeId("node-a"), "gpu1", NOW)

    assert first != second
    assert auth.worker_node(first) == NodeId("node-a")
    assert auth.worker_node(second) == NodeId("node-a")
    assert auth.worker_node(type(first)("missing")) is None
    auth.touch_worker(first, NOW + DAY)


def test_invite_scope_round_trip() -> None:
    for scope in (
        InviteScope(node=True, role=None),
        InviteScope(node=False, role=OperatorRole.VIEWER),
        InviteScope(node=True, role=OperatorRole.OPERATOR),
    ):
        assert InviteScope.decode(scope.encode()) == scope
    with pytest.raises(ValueError):
        InviteScope(node=False, role=None)
    with pytest.raises(ValueError):
        InviteScope.decode("node,admin")
    with pytest.raises(ValueError):
        InviteScope.decode("viewer,operator")
    assert OperatorRole.OPERATOR.allows(OperatorRole.VIEWER)
    assert not OperatorRole.VIEWER.allows(OperatorRole.OPERATOR)
