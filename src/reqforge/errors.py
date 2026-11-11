class ReqForgeError(Exception):
    pass


class ParseError(ReqForgeError):
    pass


class ProbeError(ReqForgeError):
    pass


class GenerationError(ReqForgeError):
    pass
