class AppError(Exception): pass
class ValidationError(AppError): pass
class PipelineError(AppError): pass
