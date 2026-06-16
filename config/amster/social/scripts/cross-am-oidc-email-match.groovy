/*
 * Cross-AM OIDC "match existing user by email" (AUTHENTICATION_TREE_DECISION_NODE).
 *
 * Runs in the SocialLogin journey on the Social Provider Handler node's
 * NO_ACCOUNT branch (i.e. the social identity is not yet linked to any local
 * account by its alias). It looks for an EXISTING local account whose `mail`
 * matches the social profile's email and, if found, makes the journey log in as
 * that account instead of provisioning a brand-new one.
 *
 * Why a script is required (see .cursor/rules + README): AM-standalone has no
 * built-in, non-IDM node that matches a user by an arbitrary attribute. The
 * Social Provider Handler node only searches by the social alias, the
 * IdentifyExistingUser node is IDM-only, and the realm identity store's
 * `users-search-attribute` is single-valued (uid), so `mail` cannot be added
 * there. The scripting engine's `idRepository` binding (ScriptIdentityRepository
 * bound to the realm) is built with an EMPTY user-search-attribute set, so its
 * getIdentity() only resolves by uid.
 *
 * Trick: ScriptIdentityRepository has a (IdentityStore, Set<String>) constructor
 * that takes explicit search attributes. We build one scoped to {mail} and reuse
 * its getIdentity(), so getIdentity(email) resolves by mail. This keeps the extra
 * scripting-engine whitelist to just AuthD + DNMapper (no raw AMIdentityRepository
 * / admin-token access). Provisioned via provision.py.
 *
 * Bindings used: nodeState, sharedState, realm (String path), logger, outcome.
 * Outcomes: "found" (existing account matched, username switched to it) and
 * "notFound" (no email match -> fall through to Provision Dynamic Account).
 */

import com.sun.identity.authentication.service.AuthD
import com.sun.identity.sm.DNMapper
import org.forgerock.openam.scripting.idrepo.ScriptIdentityRepository

def FOUND = "found"
def NOT_FOUND = "notFound"

def objectAttributes = nodeState.get("objectAttributes")
def email = null
if (objectAttributes != null && !objectAttributes.isNull() && objectAttributes.isDefined("mail")) {
    def mailValue = objectAttributes.get("mail")
    if (mailValue.isList() && mailValue.size() > 0) {
        email = mailValue.get(0).asString()
    } else if (mailValue.isString()) {
        email = mailValue.asString()
    }
}

if (email == null || email.trim().isEmpty()) {
    logger.message("cross-am email-match: no email in profile, provisioning new account")
    outcome = NOT_FOUND
    return
}

// Realm-bound identity store (admin context), searched by mail instead of uid.
def store = AuthD.getAuth().getIdentityRepository(DNMapper.orgNameToDN(realm))
def repo = new ScriptIdentityRepository(store, ["mail"] as Set)
def identity = repo.getIdentity(email)

if (identity != null) {
    def existingUsername = identity.name
    logger.message("cross-am email-match: matched existing account '" + existingUsername + "' by email " + email)
    // Switch the journey principal to the existing account so the issued session
    // is that user instead of a freshly provisioned email-named duplicate. The
    // shared-state "username" is what the tree uses as the authenticated
    // principal; the legacy `sharedState` map is what actually propagates to the
    // session (nodeState.putShared alone did not), so set both.
    sharedState.put("username", existingUsername)
    nodeState.putShared("username", existingUsername)
    outcome = FOUND
} else {
    logger.message("cross-am email-match: no existing account for " + email + ", provisioning new account")
    outcome = NOT_FOUND
}
