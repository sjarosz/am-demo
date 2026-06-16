/*
 * Cross-AM OIDC profile normalization (SOCIAL_IDP_PROFILE_TRANSFORMATION).
 *
 * Maps the partner AM's standard OpenID Connect userinfo/ID-token claims into
 * AM's normalized social profile. The handler node then runs the built-in
 * "Normalized Profile to Identity" script, which maps:
 *     givenName -> givenName, familyName -> sn, displayName -> cn,
 *     email -> mail, username -> userName, id -> alias.
 *
 * DS mandates `cn` and `sn` (and a naming attribute) on create, so this script
 * GUARANTEES displayName / familyName / username are populated even when the
 * partner OP omits name/email claims (falls back to the `sub` claim). That keeps
 * Provision Dynamic Account from failing on first (auto-registration) login.
 *
 * Bindings: `rawProfile` (JsonValue of the raw claims). Returns a JsonValue.
 */

import static org.forgerock.json.JsonValue.field
import static org.forgerock.json.JsonValue.json
import static org.forgerock.json.JsonValue.object

def str = { value, fallback ->
    (value != null && !value.isNull() && value.asString() != null && !value.asString().trim().isEmpty()) ? value.asString() : fallback
}

def sub = str(rawProfile.sub, "unknown")
def email = str(rawProfile.email, sub + "@crossam.local")
def given = str(rawProfile.given_name, str(rawProfile.name, sub))
def family = str(rawProfile.family_name, sub)
def display = str(rawProfile.name, (given + " " + family).trim())
def username = str(rawProfile.preferred_username, email)

return json(object(
        field("id", sub),
        field("displayName", display),
        field("givenName", given),
        field("familyName", family),
        field("email", email),
        field("username", username)))
