"""Cleaning browser addresses and titles before they are stored (browser-history spec, §3).

The rules only remove. They reduce what's stored; they don't promise to find every secret.
"""
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

FORMAT = "1"  # stored as meta.browser_clean_format; raise it when the rules change
MAX_DEPTH = 3  # addresses nested in parameters are cleaned this many levels deep; a deeper one is dropped

SECRET_NAMES = {"apikey", "auth", "authcode", "code", "csrf", "key", "nonce", "otp", "pass", "pwd", "rapt",
                "samlrequest", "samlresponse", "session", "sessionid", "sid", "sig", "signature", "state", "ticket",
                "usercode", "xsrf"}
IDENTITY_NAMES = {"email", "emailaddress", "loginhint", "loginidentifier", "upn", "username", "userid", "phone"}
SECRET_PARTS = ("token", "secret", "password", "passwd", "verifier", "handshake", "credential", "jwt", "jwe")
SECRET_PREFIXES = ("xamz", "xgoog")  # signed cloud-storage links
SIGN_IN_STEPS = {"activate", "activation", "auth", "callback", "confirm", "invite", "invitation", "login", "magic",
                 "oauth", "password", "reset", "signin", "sign-in", "sso", "token", "unsubscribe", "verify",
                 "verification"}

B64 = r"[A-Za-z0-9_-]"
JWT = re.compile(rf"eyJ{B64}*(?:\.{B64}*){{2}}(?:(?:\.{B64}*){{2}})?")  # a JWT has 3 parts, a JWE 5
EMAIL = re.compile(r"[^@\s/?#]+@[^@\s/?#]+\.[A-Za-z]{2,}")
OPAQUE = re.compile(rf"(?=.*[A-Za-z])(?=.*\d){B64}{{32,}}")
PATH_TOKEN = re.compile(rf"{B64}{{16,}}")
NESTED = re.compile(r"https?(?:://|%3a)", re.I)
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.I)
TITLE_MAX = 300


def name_key(name):
    """`__clerk_handshake` -> `clerkhandshake`, `token_id` -> `tokenid`: separators don't hide a name."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def secret_name(name):
    k = name_key(name)
    return (k in SECRET_NAMES or k in IDENTITY_NAMES or any(part in k for part in SECRET_PARTS)
            or k.startswith(SECRET_PREFIXES))


def clean_value(value, depth):
    """The value as kept, or None when it must go."""
    if JWT.fullmatch(value) or EMAIL.fullmatch(value):
        return None
    if NESTED.match(value):
        if depth >= MAX_DEPTH:
            return None  # too deep to clean: dropped, never kept as it is
        inner = unquote(value) if value[:8].lower().startswith(("http%3a", "https%3a")) else value
        return clean_url(inner, depth + 1)
    return value


def clean_params(text, depth):
    pairs = parse_qsl(text, keep_blank_values=True)
    kept = []
    for name, value in pairs:
        if secret_name(name):
            continue
        value = clean_value(value, depth)
        if value is not None:
            kept.append((name, value))
    if kept == pairs:
        return text  # nothing removed: keep the browser's own spelling
    return urlencode(kept, safe="/:@", quote_via=quote)


def clean_fragment(fragment, depth):
    bare = unquote(fragment)  # one layer, decoded: a mix of encoded and literal separators can't hide a token
    if bare.startswith(("/", "!/")) and "?" in bare:  # an app route with parameters: #/callback?…
        route, _, query = bare.partition("?")
        query = clean_params(query, depth)
        cleaned = route + ("?" + query if query else "")
    elif "=" in bare:
        cleaned = clean_params(bare, depth)
    elif JWT.fullmatch(bare) or OPAQUE.fullmatch(bare):
        return ""
    else:
        return fragment
    return fragment if cleaned == bare else cleaned  # nothing removed: keep the browser's own spelling


def clean_path(path):
    parts = path.split("/")
    out = []
    for i, part in enumerate(parts):
        text = unquote(part)
        after_sign_in = i > 0 and unquote(parts[i - 1]).lower() in SIGN_IN_STEPS
        out.append("…" if JWT.fullmatch(text) or (after_sign_in and PATH_TOKEN.fullmatch(text)) else part)
    return "/".join(out)


def clean_url(url, depth=0):
    """The address as stored, or None when it isn't http(s) with a host, or can't be read."""
    try:
        s = urlsplit(url)
        host = s.hostname
    except ValueError:
        return None
    if s.scheme.lower() not in ("http", "https") or not host:
        return None
    netloc = s.netloc.rpartition("@")[2]  # user:password@ is dropped
    query = clean_params(s.query, depth) if s.query else ""
    fragment = clean_fragment(s.fragment, depth) if s.fragment else ""
    return urlunsplit((s.scheme, netloc, clean_path(s.path), query, fragment))


def clean_title(title):
    """Titles keep their words; addresses inside them are cleaned like any other. Clipped to 300 characters."""
    if not title:
        return title
    return URL_IN_TEXT.sub(lambda m: clean_url(m.group(0)) or "", title)[:TITLE_MAX]
