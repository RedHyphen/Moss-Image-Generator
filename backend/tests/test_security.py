from app.security import hash_password, new_token, verify_password


def test_password_hash_round_trip():
    encoded = hash_password("correct horse battery staple")
    assert encoded != "correct horse battery staple"
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_tokens_are_unique():
    assert new_token() != new_token()
