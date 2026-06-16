/*
 * Cross-AM OIDC "Normalized Profile to Identity" (SOCIAL_IDP_PROFILE_TRANSFORMATION).
 *
 * Runs inside the Social Provider Handler node AFTER the provider's transform
 * script has produced `normalizedProfile`. It maps the normalized social profile
 * onto the AM identity attributes used for account lookup and dynamic provisioning.
 *
 * Difference from the built-in "Normalized Profile to Identity" script: it also
 * sets `uid` (= email). The Provision Dynamic Account node's DefaultAccountProvider
 * uses the `uid` attribute as the account's username and, when `uid` is absent,
 * falls back to a random UUID (which is why dynamically provisioned social users
 * were getting GUID usernames). Setting `uid` to the email makes the email the
 * username for genuinely new accounts.
 *
 * Existing accounts are matched earlier by email (Identify Existing User node), so
 * this provisioning path only runs for users with no existing email match.
 *
 * Bindings: `normalizedProfile` (JsonValue), `selectedIdp` (String). Returns a JsonValue.
 */

import static org.forgerock.json.JsonValue.field
import static org.forgerock.json.JsonValue.json
import static org.forgerock.json.JsonValue.object

def email = normalizedProfile.email.asString()

return json(object(
        field("givenName", normalizedProfile.givenName),
        field("sn", normalizedProfile.familyName),
        field("mail", normalizedProfile.email),
        field("cn", normalizedProfile.displayName),
        field("userName", email),
        field("uid", email),
        field("iplanet-am-user-alias-list", selectedIdp + '-' + normalizedProfile.id.asString())))
