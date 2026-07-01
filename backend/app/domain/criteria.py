from pydantic import BaseModel
class Criterion(BaseModel):
    row: int; name: str
class ExtractionPlan(BaseModel):
    criterion: str; strategy: str; target_sections: list[str]; expected_type: str="text"; requires_calculation: bool=False
