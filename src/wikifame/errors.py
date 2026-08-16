class WikiFameError(Exception):
    code = "wikifame_error"
    permanent = False


class RetryableUpstreamError(WikiFameError):
    code = "upstream_unavailable"


class PermanentDataError(WikiFameError):
    code = "invalid_page"
    permanent = True


class ResponseTooLargeError(PermanentDataError):
    code = "wikiwho_response_too_large"
