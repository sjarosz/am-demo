/*
 * SAML2 NameID Mapper script (context: SAML2_NAMEID_MAPPER) - "samllab" proof
 * point (configured on the org IdP's REMOTE view of the com SP).
 *
 * Based on the PingAM 8.1 sample "SAML2 NameID Mapper Script", which delegates
 * to the configured Java plugin via the nameIDScriptHelper binding to obtain the
 * default NameID value. Here we prefix that value with the star emoji so the
 * customization is obvious as the assertion Subject NameID surfaced into the SP
 * session by the SP Adapter.
 *
 * Note: the SP auto-federates on the `uid` SAML attribute (not the NameID), so a
 * star-tagged NameID is purely a visible marker and does not affect account
 * linking.
 *
 * Bindings: nameIDScriptHelper, logger.
 * Returns: String - the SAML2 NameID value.
 */
(function () {
    var base = nameIDScriptHelper.getNameIDValue();
    if (base == null) {
        return null;
    }
    return "\u2B50" + base;
}());
