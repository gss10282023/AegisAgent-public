from agentprog import agentprog_pipeline, AgentProgConfig


config = AgentProgConfig(
    task_description="Open Chrome, search for AgentProg.",
    serial="emulator-5554",
)
agentprog_pipeline(config)

