class WikiPeopleError(Exception):
    code = "wikipeople_error"
    permanent = False


class RetryableUpstreamError(WikiPeopleError):
    code = "upstream_unavailable"


class PermanentDataError(WikiPeopleError):
    code = "invalid_page"
    permanent = True


class ResponseTooLargeError(PermanentDataError):
    code = "wikiwho_response_too_large"
