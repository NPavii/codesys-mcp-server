import type { KnowledgeDocument } from "./types.js";
import { officialSourceUrls } from "./sources.js";

export const curatedDocs: KnowledgeDocument[] = [
  {
    id: "official-codesys-help-and-examples",
    title: "Official CODESYS Help And Examples",
    summary:
      "Use the CODESYS Online Help and CODESYS Examples index as primary official sources for documentation and sample projects.",
    tags: [
      "online-help",
      "examples",
      "sample-projects",
      "official-docs",
      "documentation",
      "reference"
    ],
    citations: [
      {
        title: "CODESYS Online Help",
        url: officialSourceUrls.onlineHelpRoot
      },
      {
        title: "CODESYS Examples",
        url: officialSourceUrls.examplesStartPage
      }
    ],
    body: `
The CODESYS Online Help is the primary official documentation entry point. Prefer it when the user asks for CODESYS-specific behavior, UI workflows, library documentation, language syntax, or runtime features.

The CODESYS Examples page is the primary official index for sample projects. It is useful when the user asks for practical examples, reference projects, or implementation patterns across areas such as the Development System, fieldbuses, visualization, SoftMotion, CNC, robotics, communication, file utilities, date/time handling, object-oriented programming, task management, and network variables.

When answering with examples:
- Use the examples page as a pointer to official sample projects instead of inventing a complete project structure.
- Explain which example category appears relevant and why.
- Remind the user to verify add-on requirements, library versions, target runtime support, and device-specific constraints before applying a sample project to a real machine.
`.trim()
  },
  {
    id: "codesys-overview",
    title: "CODESYS Development System V3 Overview",
    summary:
      "CODESYS Development System V3 is an IEC 61131-3 engineering environment for industrial controller applications.",
    tags: ["overview", "development-system", "iec-61131-3", "v3"],
    citations: [
      {
        title: "CODESYS Development System Overview",
        url: officialSourceUrls.developmentSystem
      },
      {
        title: "CODESYS Development System Product Page",
        url: officialSourceUrls.developmentSystemProduct
      }
    ],
    body: `
CODESYS Development System V3 is used to create controller applications for PLCs, PACs, soft PLCs, and other automation devices. It is built around IEC 61131-3 concepts: applications contain POUs, variables, libraries, task configuration, device configuration, and optional features such as visualization, motion, fieldbus integration, and online debugging.

When helping a user, identify the target runtime and library versions early. CODESYS projects can depend on device-specific I/O mappings, library placeholders, package versions, and runtime features. General IEC 61131-3 advice is useful, but final code should always be compiled and tested inside the actual CODESYS project.

Good first questions for CODESYS work:
- Which CODESYS version and runtime/device are you targeting?
- Is the code Structured Text, FBD/LD, SFC, or another editor?
- Is this a Program, Function Block, Function, Method, or library object?
- Are safety, motion, fieldbus, or hardware I/O involved?
`.trim()
  },
  {
    id: "structured-text-basics",
    title: "Structured Text Basics",
    summary:
      "Structured Text is the main textual IEC 61131-3 language in CODESYS; statements end with semicolons and variables are declared separately.",
    tags: ["structured-text", "st", "syntax", "iec-61131-3"],
    citations: [
      {
        title: "Programming in Structured Text",
        url: officialSourceUrls.programmingInSt
      },
      {
        title: "Programming Languages and Editors",
        url: officialSourceUrls.programmingLanguages
      }
    ],
    body: `
Structured Text (ST) is the CODESYS textual programming language used for logic that benefits from expressions, assignments, conditionals, loops, and function or function block calls. A POU editor normally has a declaration area and an implementation area. Declarations define variables and POU interfaces; implementation code performs the logic.

Core ST habits:
- End executable statements with a semicolon.
- Keep declarations close to purpose: VAR_INPUT for caller-provided inputs, VAR_OUTPUT for reported outputs, VAR for retained instance state inside a function block or program, and VAR_TEMP for temporary method/function data when appropriate.
- Prefer explicit state names and meaningful variable names over dense one-line expressions.
- Avoid unbounded loops in cyclic PLC logic unless there is a clear upper bound.
- Treat generated ST as a draft that must compile in the target CODESYS project.

Example:

FUNCTION_BLOCK FB_DebouncedStart
VAR_INPUT
    xStart : BOOL;
    tDelay : TIME := T#250ms;
END_VAR
VAR_OUTPUT
    xReady : BOOL;
END_VAR
VAR
    tonDelay : TON;
END_VAR

tonDelay(IN := xStart, PT := tDelay);
xReady := tonDelay.Q;
`.trim()
  },
  {
    id: "pou-selection",
    title: "Choosing The Right POU Type",
    summary:
      "Programs, function blocks, and functions have different state and call semantics in CODESYS.",
    tags: ["pou", "program", "function-block", "function", "architecture"],
    citations: [
      {
        title: "Object: POU",
        url: officialSourceUrls.pou
      },
      {
        title: "Object: Function Block",
        url: officialSourceUrls.functionBlock
      },
      {
        title: "Object: Function",
        url: officialSourceUrls.function
      }
    ],
    body: `
A POU is a Programming Organization Unit. In typical CODESYS application code, choose among:

Program:
- Use for top-level cyclic application logic called by a task.
- Good for orchestrating I/O, calling function block instances, and coordinating application sections.

Function block:
- Use when logic needs instance state, timers, edge detection, configuration, modes, or persistent behavior between calls.
- Call through an instance. Each instance has its own memory.
- Good for reusable machine modules such as pumps, valves, conveyors, sequencers, alarms, or communication handlers.

Function:
- Use for stateless calculations that return one value and should behave the same for the same inputs.
- Avoid hidden state, global variables, direct I/O, timers, or side effects in functions.

Default recommendation: if the logic needs memory across PLC cycles, make a function block. If it is a pure calculation, make a function. If it is the top-level task entry point, use a program.
`.trim()
  },
  {
    id: "function-blocks",
    title: "Function Block Design",
    summary:
      "Function blocks encapsulate stateful logic and are called through instances in CODESYS.",
    tags: ["function-block", "fb", "state", "oop", "structured-text"],
    citations: [
      {
        title: "Object: Function Block",
        url: officialSourceUrls.functionBlock
      },
      {
        title: "ST Function Block Call",
        url: officialSourceUrls.functionBlockCall
      },
      {
        title: "Object-Oriented Programming",
        url: officialSourceUrls.objectOriented
      }
    ],
    body: `
A function block is the normal unit for reusable stateful PLC behavior. You declare an instance and call that instance each scan. Inputs are assigned on the call, internal VAR values and outputs belong to that instance, and outputs can be read after the call.

Typical ST call pattern:

VAR
    fbMotor : FB_Motor;
END_VAR

fbMotor(
    xEnable := xMachineReady,
    xStart := xStartCommand,
    xStop := xStopCommand
);

xMotorRunCommand := fbMotor.xRunCommand;

Design tips:
- Make inputs commands or measurements, not direct hidden reads from globals.
- Make outputs observable state and commands.
- Keep hardware-specific mapping at the program or I/O boundary when possible.
- Put repeated behavior in methods only when it improves clarity.
- Be cautious with inheritance. Composition and small function blocks are often easier to maintain in PLC projects.
`.trim()
  },
  {
    id: "timers",
    title: "Timers In Structured Text",
    summary:
      "Standard timers such as TON, TOF, and TP are function blocks that must be instantiated and called every cycle.",
    tags: ["timer", "ton", "tof", "tp", "time", "standard-library"],
    citations: [
      {
        title: "TON Timer Function Block",
        url: officialSourceUrls.ton
      },
      {
        title: "ST Function Block Call",
        url: officialSourceUrls.functionBlockCall
      },
      {
        title: "TIME and LTIME Constants",
        url: officialSourceUrls.timeConstants
      }
    ],
    body: `
Timers in the Standard library are function blocks, so they need an instance. Call the timer instance every PLC cycle where you need it to update. The common TON pattern is a turn-on delay: when IN becomes TRUE, elapsed time accumulates until PT is reached, then Q becomes TRUE. When IN is FALSE, the TON resets.

Example:

VAR
    tonStartDelay : TON;
    xDelayedStart : BOOL;
END_VAR

tonStartDelay(IN := xStartCommand, PT := T#2s);
xDelayedStart := tonStartDelay.Q;

Practical guidance:
- Declare timer instances in VAR of a program or function block, not as a temporary variable that is recreated each call.
- Use TIME literals such as T#500ms, T#2s, or T#1m30s when the target library supports them.
- Read Q and ET after calling the timer.
- For safety-related timing, verify library behavior, runtime behavior, scan time, and machine safety requirements.
`.trim()
  },
  {
    id: "state-machines",
    title: "State Machine Pattern",
    summary:
      "Structured Text state machines are a clear way to organize modes, sequences, and step logic.",
    tags: ["state-machine", "sequence", "structured-text", "design"],
    citations: [
      {
        title: "Programming in Structured Text",
        url: officialSourceUrls.programmingInSt
      },
      {
        title: "Object: Function Block",
        url: officialSourceUrls.functionBlock
      }
    ],
    body: `
State machines are a common CODESYS Structured Text pattern for sequences, modes, and device control. Use an explicit state variable, one CASE statement, and clear transition conditions. Keep entry actions, continuous actions, and transition logic easy to inspect.

Example sketch:

TYPE E_PumpState :
(
    Idle,
    Starting,
    Running,
    Faulted
);
END_TYPE

CASE eState OF
    E_PumpState.Idle:
        xRunCommand := FALSE;
        IF xStartRequest THEN
            eState := E_PumpState.Starting;
        END_IF

    E_PumpState.Starting:
        xRunCommand := TRUE;
        IF xFeedback THEN
            eState := E_PumpState.Running;
        ELSIF xFault THEN
            eState := E_PumpState.Faulted;
        END_IF

    E_PumpState.Running:
        xRunCommand := TRUE;
        IF xStopRequest THEN
            eState := E_PumpState.Idle;
        END_IF

    E_PumpState.Faulted:
        xRunCommand := FALSE;
        IF xReset AND NOT xFault THEN
            eState := E_PumpState.Idle;
        END_IF
END_CASE

Guidance:
- Prefer enum states over raw integers.
- Add timeout timers where transitions can hang.
- Keep safety interlocks outside or above the normal sequence logic when required by the machine design.
`.trim()
  },
  {
    id: "variables-and-naming",
    title: "Variables And Naming",
    summary:
      "Use clear scopes, types, and names so CODESYS code can be monitored and maintained during commissioning.",
    tags: ["variables", "naming", "style", "structured-text"],
    citations: [
      {
        title: "Programming in Structured Text",
        url: officialSourceUrls.programmingInSt
      },
      {
        title: "Object: POU",
        url: officialSourceUrls.pou
      }
    ],
    body: `
CODESYS projects are maintained under online monitoring and commissioning pressure, so variable names should be boring and clear. Common prefixes can help when a team uses them consistently, for example x for BOOL, i for INT, r for REAL, t for TIME, e for enum, fb for function block instance, and st for structure. Do not force a prefix scheme if the project already has a standard.

Scope guidance:
- VAR_INPUT: values passed in from the caller.
- VAR_OUTPUT: values exposed back to the caller.
- VAR_IN_OUT: references that the block can read and write; use carefully because it couples the caller and callee.
- VAR: internal state for a program or function block.
- VAR_TEMP: temporary values for one call, often inside methods or functions.

Prefer named constants or parameters for configurable times, limits, and thresholds. Avoid magic values embedded in logic.
`.trim()
  },
  {
    id: "libraries",
    title: "Libraries And Standard Blocks",
    summary:
      "CODESYS libraries provide reusable POUs; generated guidance should respect library versions and placeholders.",
    tags: ["libraries", "standard-library", "dependencies", "versions"],
    citations: [
      {
        title: "Standard Library Documentation",
        url: officialSourceUrls.standardLibrary
      },
      {
        title: "CODESYS Development System Overview",
        url: officialSourceUrls.developmentSystem
      }
    ],
    body: `
CODESYS libraries provide reusable functions, function blocks, data types, and interfaces. Standard IEC-style blocks such as timers, counters, triggers, and string functions typically come from the Standard library. Other features may come from device vendor libraries, CODESYS add-on libraries, or application-specific libraries.

Guidance:
- Confirm the library name and version in the Library Manager before relying on an API.
- Do not invent library functions. If a function is not found in the project or official docs, state that it needs verification.
- Watch for namespace or placeholder differences between projects.
- Keep vendor-specific library use isolated when possible, so reusable application logic remains portable.
`.trim()
  },
  {
    id: "debugging-and-online",
    title: "Debugging And Online Checks",
    summary:
      "CODESYS online tools help monitor variables, tasks, breakpoints, and runtime behavior during commissioning.",
    tags: ["debugging", "monitoring", "online", "tasks", "commissioning"],
    citations: [
      {
        title: "CODESYS Development System Overview",
        url: officialSourceUrls.developmentSystem
      },
      {
        title: "Task Configuration Monitoring",
        url: officialSourceUrls.taskMonitoring
      }
    ],
    body: `
CODESYS supports online workflows such as variable monitoring, breakpoints, task monitoring, and application download/online change depending on target support. When code is generated or changed, the acceptance path should include compile, online monitoring, forcing only when appropriate, and runtime checks on the target device or simulator.

Troubleshooting checklist:
- Build the project and fix type errors before runtime investigation.
- Confirm library versions and missing placeholders.
- Check task configuration, cycle time, and whether the POU is actually called.
- Monitor inputs, internal state, timers, outputs, and fault bits.
- For I/O problems, verify device mapping and physical signals separately from logic.
`.trim()
  },
  {
    id: "safety-motion-fieldbus-warning",
    title: "Safety, Motion, Fieldbus, And Hardware I/O",
    summary:
      "Hardware-specific and safety-related CODESYS answers must be verified against the exact device and project.",
    tags: ["safety", "motion", "fieldbus", "hardware", "io"],
    citations: [
      {
        title: "CODESYS Development System Overview",
        url: officialSourceUrls.developmentSystem
      }
    ],
    body: `
For safety, motion, fieldbus, and hardware I/O topics, general CODESYS guidance is not enough. The correct answer depends on the device, runtime, bus stack, library version, firmware, machine wiring, safety architecture, and applicable standards.

The assistant should:
- State uncertainty when the exact device or library is unknown.
- Ask the user to verify against the device manual and CODESYS project Library Manager.
- Avoid claiming that generated code is safe for machine operation.
- Recommend simulation, commissioning checks, and formal safety validation where relevant.
`.trim()
  },
  {
    id: "style-guidance",
    title: "CODESYS Structured Text Style Guidance",
    summary:
      "Readable CODESYS ST favors explicit declarations, simple conditions, small reusable function blocks, and monitorable state.",
    tags: ["style", "best-practice", "structured-text", "maintainability"],
    citations: [
      {
        title: "Programming in Structured Text",
        url: officialSourceUrls.programmingInSt
      },
      {
        title: "Object: Function Block",
        url: officialSourceUrls.functionBlock
      }
    ],
    body: `
Good Structured Text for industrial control is easy to monitor, easy to commission, and conservative about hidden behavior.

Recommended style:
- Name commands, feedback, faults, and states explicitly.
- Prefer one clear state machine over scattered transition bits.
- Keep function blocks focused on one device, machine module, or behavior.
- Call stateful function blocks exactly once per intended cycle unless there is a deliberate reason.
- Keep pure calculations in functions and stateful behavior in function blocks.
- Add comments for machine intent or non-obvious timing, not for trivial assignments.
- Keep generated code aligned with the project's existing naming and formatting conventions.
`.trim()
  }
];
