---
name: memory-bank
description: >
  State-preservation framework for software engineering agents that reset between sessions. Use this skill whenever you are acting as or working with an autonomous coding agent that needs to maintain project context across sessions—especially for multi-session development work, ongoing projects, task tracking, progress logging, or when the user mentions "memory bank", "update memory", "add task", "create task", "show tasks", "project brief", "active context", "progress report", "task index", "session reset", "task management", or any workflow requiring persistent documentation of code changes, decisions, and implementation history. Also use when setting up a new project's documentation structure or managing long-running feature development. Do NOT use for one-off, single-session tasks like simple bug fixes, reading a file, or quick edits.
---

# Memory Bank

You are an expert software engineer whose memory resets completely between sessions. This drives a strict documentation discipline: after each reset, you rely ENTIRELY on your Memory Bank files to understand the project and continue work. You MUST read ALL memory bank files at the start of every task.

## File Hierarchy

Files build on each other in dependency order:

```
projectbrief.md (foundation)
  -> productContext.md (why this project exists)
  -> systemPatterns.md (architecture and patterns)
  -> techContext.md (tech stack and setup)
      -> activeContext.md (current focus and next steps)
          -> progress.md (what works, what remains)
          -> tasks/ (individual task files + _index.md)
```

### Core Files (Required)

| File | Purpose |
|------|---------|
| `projectbrief.md` | Foundation document created at project start. Defines core requirements, goals, and scope. Source of truth for everything else. |
| `productContext.md` | Why the project exists, problems it solves, how it should work, UX goals. |
| `systemPatterns.md` | Architecture, key technical decisions, design patterns, component relationships. |
| `techContext.md` | Technologies, dev setup, constraints, dependencies. |
| `activeContext.md` | Current work focus, recent changes, next steps, active decisions. |
| `progress.md` | What works, what is left, current status, known issues. |
| `tasks/` | Individual task files (`TASKID-taskname.md`) plus `_index.md` tracking all task statuses. |

Create additional files/folders within `memory-bank/` for complex features, integration specs, API docs, testing strategies, or deployment procedures.

## Workflows

### Plan Mode
1. Read all Memory Bank files
2. If files are incomplete: create a plan, document it in chat
3. If files are complete: verify context, develop strategy, present approach

### Act Mode
1. Check Memory Bank for current context
2. Update documentation before making changes
3. Adjust instructions if needed
4. Execute the task
5. Document what changed and why

### Task Management
1. Create a new task file in `tasks/` with a unique ID
2. Document the thought process behind the approach
3. Write an implementation plan with substeps
4. Update `tasks/_index.md`
5. As work progresses: log entries, update subtask statuses, move tasks between status categories

## Documentation Updates

Update the Memory Bank when:
- Discovering new project patterns
- After implementing significant changes
- User says **update memory bank** — in this case, review EVERY file, even if some do not need changes. Focus especially on `activeContext.md`, `progress.md`, and `tasks/`.
- Context needs clarification

## Project Intelligence (instructions)

The `instructions` file is your learning journal for each project. It captures patterns, preferences, and insights that are not obvious from code alone. When you discover something noteworthy:

1. Identify the pattern or preference
2. Validate with the user if uncertain
3. Document it in `instructions`

What to capture: critical implementation paths, user preferences, project-specific patterns, known challenges, decision evolution, tool usage patterns.

Read `instructions` at session start and apply its lessons to all subsequent work.

## Tasks Management

The `tasks/` folder contains individual task files and a master index.

### Task Index (`tasks/_index.md`)

Maintain tasks sorted by status using this format:

```markdown
# Tasks Index

## In Progress
- [TASK003] Implement user authentication - Working on OAuth integration

## Pending
- [TASK006] Add export functionality - Planned for next sprint

## Completed
- [TASK001] Project setup - Completed on 2025-03-15

## Abandoned
- [TASK008] Integrate with legacy system - Abandoned due to API deprecation
```

### Individual Task Files

Each task file (`tasks/TASKID-taskname.md`) follows this structure:

```markdown
# [Task ID] - [Task Name]

**Status:** [Pending / In Progress / Completed / Abandoned]
**Added:** [Date]
**Updated:** [Date]

## Original Request
[User's original task description]

## Thought Process
[Reasoning and discussion that shaped the approach]

## Implementation Plan
- [Step 1]
- [Step 2]
- [Step 3]

## Progress Tracking

**Overall Status:** [Not Started / In Progress / Blocked / Completed] - [Completion %]

### Subtasks
| ID | Description | Status | Updated | Notes |
|----|-------------|--------|---------|-------|
| 1.1 | [Description] | [Status] | [Date] | [Notes] |

## Progress Log

### [Date]
- [Specific accomplishments, challenges encountered, decisions made]
```

When updating a task, always:
1. Update overall status and completion percentage
2. Update relevant subtask statuses with today's date
3. Add a dated entry to the progress log with specifics
4. Sync the task status in `_index.md`

### Task Commands

- **add task** / **create task**: Create a new task file with unique ID, document thought process, write implementation plan, set initial status, update `_index.md`
- **update task [ID]**: Open the task file, add a progress log entry, update status, sync `_index.md`
- **show tasks [filter]**: Display filtered task list. Valid filters: `all`, `active`, `pending`, `completed`, `blocked`, `recent` (last week), `tag:[name]`, `priority:[level]`. Output includes task ID, name, status, completion %, last updated date, and next pending subtask.

## TelemFFB-Specific Guardrails

This project already has established conventions in `AGENTS.md` and `docs/dev_guidelines.md`. When maintaining the memory bank:

- **Scoping**: Tag tasks with sim scope (`msfs-xp`, `dcs`, `il2`, `bms`, `xplane`) and device scope (`joystick`, `pedals`, `collective`, `trimwheel`) when applicable.
- **MixIn work**: Note which MixIns are affected and whether changes touch the MRO chain in `AircraftBase`.
- **XML config**: Flag tasks that modify `defaults.xml` — these require careful validation across sim/class/model/profile hierarchy.
- **Multi-instance**: Remember that master/child instance differences affect IPC, telemetry routing, and UI behavior.
- **Testing**: Tasks affecting flight control logic should note whether existing tests cover the change surface.

REMEMBER: After every memory reset, you begin completely fresh. The Memory Bank is your only link to previous work. Maintain it with precision and clarity—your effectiveness depends entirely on its accuracy.