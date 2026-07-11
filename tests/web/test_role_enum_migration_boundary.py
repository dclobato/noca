import logging

from jwtservice import JWTService, load_token_config_from_dict

from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationDetails
from web.services.authentication_service import AuthAction, AuthenticationService
from web.services.contest_user_service import parse_single_user_role

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"


class _NoopGeo:
    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


def test_parse_single_user_role_accepts_canonical_values_only() -> None:
    assert parse_single_user_role("JUDGE") == RoleEnum.JUDGE
    assert parse_single_user_role("TEAM") == RoleEnum.TEAM

    for raw_role in ("j", "t", "judge", "team"):
        try:
            parse_single_user_role(raw_role)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected {raw_role!r} to be rejected")


async def test_user_login_emits_canonical_role_audience(
    session,
    running_contest,
    judge_user,
) -> None:
    jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=AuthAction,
    )
    auth_service = AuthenticationService(jwt_service=jwt_service, geolocation_service=_NoopGeo())

    token = await auth_service.user_login(
        username=judge_user.username,
        password="TestPass1!",
        contest_id=running_contest.id,
        session=session,
    )
    result = jwt_service.validate(token)

    assert result.valid is True
    assert result.aud == RoleEnum.JUDGE.value
