from pydantic import BaseModel, ConfigDict, Field, model_validator

from .datalake_tools import DATA_QA_USE, is_retired_datalake_registration


class ToolGroupConfig(BaseModel):
    """Config section for a tool group"""

    name: str = Field(..., description="Unique name for the tool group")
    model_config = ConfigDict(extra="allow")


class ToolConfig(BaseModel):
    """Config section for a tool"""

    name: str = Field(..., description="Unique name for the tool")
    group: str = Field(..., description="Group name for the tool")
    use: str = Field(
        ...,
        description="Variable name of the tool provider(e.g. deerflow.sandbox.tools:bash_tool)",
    )
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_datalake_migration(self):
        if is_retired_datalake_registration(self.name, self.use):
            raise ValueError("Retired DataLake tool registration; migrate the versioned configuration to datalake_data_qa before deployment")
        if (self.name == "datalake_data_qa") != (self.use == DATA_QA_USE):
            raise ValueError("datalake_data_qa requires its matching provider export and configuration name")
        return self
