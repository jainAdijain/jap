"""The contract between JAP and an outside AI harness (PRD 1.30, AH2).

The reader and writer exports carry `contract_version`, and the JSON Schemas in
agent/contract/ in the repo describe both exports and imports at this version. A harness
adapter, a job prompt or the runner (agent/run.sh) may rely on anything the schemas promise.
A change that would break one of them (a key renamed or removed, a reply shape changed) bumps
this number together with the schemas and the job prompts; a key added is not a break.
"""

CONTRACT_VERSION = 1
