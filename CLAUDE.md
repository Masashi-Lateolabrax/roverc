# roverc — Undergraduate Thesis Project

## What this project is

For an undergraduate student's (heading to industry) graduation thesis, build a research theme and implementation platform around M5StickC Plus2 + RoverC (a mecanum-wheel car) + PC (Python, over LAN).

## Information Architecture

`CLAUDE.md` stays compact. Detail lives in one of the four stores below — when adding information, pick the one whose purpose matches.

- **Docs** — Long-lived knowledge that is expensive to obtain or reproduce, and that collaborators need to share. Code design decisions (settled through dialogue), experiment results (long to run), literature surveys (long to research), development policies (settled through dialogue). *(The plugin is agnostic to the documentation tool — Zola, mdBook, Sphinx, plain markdown in `docs/`, etc. Each project picks its own.)*
- **Memory** (`.claude/memories/` project; `~/.claude/memories/` global) — The AI self-reinforcement and reference system. Entries are markdown files in three subfolders: `static/unfold/` (always injected, with how-to detail), `static/fold/` (always injected as pointer only — for handoff notes, file trees, reference material that should be available every session), `dynamic/` (injected on semantic match with the prompt). One entry = one theme. Global memories cross projects.
- **Skills** (`.claude/skills/<name>/SKILL.md`) — Named bundles of task-specific instructions with tool-permission scoping. Each `SKILL.md` has a `description` explaining what the skill does and `allowed-tools` listing the tools it may use. Examples from this plugin: `commit`, `pull-request`, `issues`, `clean-branch`. **Skills do not always fire on their own**, so a project's `CLAUDE.md` typically keeps a dispatch table (e.g. *"When committing, use `git-cc-plugin:git-for-claude`"*) to reinforce invocation. Those reinforcement entries are load-bearing and belong in `CLAUDE.md` — they are not candidates for offloading.
- **CLAUDE.md** (this file) — Kept compact. Project-specific operating context that must load in every session: what the project is, which stores exist, and a dispatch table pointing to the relevant store or skill for each concern. Behavior-shaping rules belong in Memory, not here.

## Store / Skill dispatch

- **Commit / PR / Issue / Milestone operations** → use `git-cc-plugin:git-for-claude` (the only git plugin enabled in this project; raw `git add`/`commit`/`push` are denied, and this skill is the only write path; a single skill covers commit/PR/issue).
- **Memory entry operations** → no dedicated skill is installed (`lab-tool` is disabled). Edit files under `.claude/memories/` directly.
- ⚠ Before dispatching, always confirm the skill appears in this session's available-skills list. A skill from a plugin not in `enabledPlugins` (`.claude/settings.local.json`) cannot be invoked.
- **Platform bring-up detail** → `docs/platform_bringup.md`
- **Camera-streaming debug lessons** (MJPEG / urllib `read1` coalescing / Wire1 self-heal / I2C bus recovery / XCLK 8MHz, etc.) → `docs/camera_streaming_lessons.md`
- **Alternate-line research program candidate (for students planning grad school)** (assumes stereo; not mounted on the current rig = forward monocular, a future line) → `docs/async_stereo_interpolation.md`
- **Fisheye camera + IMU VIO velocity-estimation roadmap** (assumes fisheye; not mounted on the current rig = forward monocular, a future line) → `docs/fisheye_vio_roadmap.md`
- **RoverC hardware spec** → `docs/roverc_datasheet.pdf` / `docs/roverc_pro_datasheet.pdf` / `docs/roverc_i2c_protocol.pdf`
- **StickC Plus2 schematic** → `docs/stickc_plus2_schematic.pdf`

## People

- **Advisor** (owner of this repo): has experience in complex-systems / swarm research. The designer of the research program.
- **Student** (implementer): almost no programming experience, prefers hardware-leaning work, heading to industry (no PhD track).

## Division of work

### Advisor's scope: platform bring-up
The advisor **completes the platform foundation single-handedly** (RoverC teleoperation → monocular camera → time synchronization → performance evaluation; the old stage 4 "stereo conversion" was retired by the 2026-06-09 switch to monocular) and hands it off to the student as a verified platform. Detail in `docs/platform_bringup.md`.

### Student's scope: the Mediator layer
On the handed-off platform, the student is responsible for the multi-operator UI, synchronized recording, baseline Mediator implementations, running experiments, data-quality verification, and writing the thesis. Detail in this file's "First-cell composition of the thesis" and "Student's annual schedule" sections.

## Related documents

- `docs/platform_bringup.md`: the advisor's 5-stage platform bring-up roadmap
- `docs/async_stereo_interpolation.md`: alternate-line research program candidate (preserved for students planning grad school; independent of this thesis = the Mediator program)
- `docs/fisheye_vio_roadmap.md`: velocity-estimation roadmap via Timer Camera F + IMU monocular fisheye VIO (independent of platform bring-up; can be started after stage 3 is complete)

## Structure of the research

### Upper frame (the advisor's long-term research program)

**"Building systems that appropriately mediate among competing players."**

Concretely: a long-term program that fills in a two-dimensional Mediator × Domain experience map for the design of a **Mediator** when multiple operators drive a single robot simultaneously.

The theoretical background is social choice theory (Arrow's impossibility theorem, etc.). On the deductive fact that no perfect mediator exists, the ultimate goal is to **empirically extract "the conditions for a good Mediator."**

### The big goal (multiple students, on a multi-year scale)

**Building and evaluating a learning-based Mediator (an NN-based fusion engine)**

Feed the NN (the situation + the instructions of multiple operators) and have it decide how to weight whose instruction and which, or generate the final command itself. This amounts to a real-time, continuous-valued version of crowdsourcing aggregation (the Dawid-Skene family).

### This year's thesis (the student's responsibility)

**Building a data-collection platform for learning-based Mediator research**

The student does not touch the ML core itself but handles the **preliminary stage** to it. Concretely, three layers:

1. **Establishing the means to build the data-collection environment**
   - Standardizing the physical task environment (obstacle placement, course, goal)
   - Calibration procedures, documentation for reproducibility
   - Deliverables: experiment protocol + checklist + calibration procedure manual

2. **The data-collection platform (the system)**
   - A UI multiple operators can input to (joystick / browser / keyboard)
   - Sending RoverC commands via the StickC
   - Recording all data while keeping it synchronized (each person's input, robot state, task status, timestamps)
   - Output in a form usable for ML training (CSV / JSONL / parquet, etc.)
   - Deliverables: Python library + UI + documentation

3. **Collecting and verifying real data**
   - Running real sessions using the platform
   - Data-quality verification (quantifying noise, dropouts, sync skew)
   - Implement a few **fixed Mediators as baselines** and gather data with them (around 2-3 of averaging, dominance, voting)
   - Deliverables: dataset + quality report + baseline results

## The thesis narrative

> **Problem**: Learning-based Mediator research needs high-quality real-time data from multiple operators, but existing collection is ad hoc, assumes haptic devices, has a high barrier to entry, and is poorly reproducible.
>
> **Objective**: Establish a platform and methodology that can reproducibly collect real-time multi-operator teleoperation data on low-cost hardware (RoverC class).
>
> **Outcome**: The three layers above + an initial dataset + baseline Mediator results.
>
> **Positioning**: This foundation enables subsequent learning-based Mediator research. It is the first step in building the Mediator × Domain experience map.

## First-cell composition of the thesis

### Hardware
- RoverC × 1
- M5StickC Plus2 × 1 (operational, confirmed 2026-04-26)
- M5StickC (plain, spare, for swap)
- M5Stack Timer Camera X × 5 (1 operational = forward monocular on RoverC, 4 spare. **Changed from a forward-stereo 2-camera setup to a monocular 1-camera setup on 2026-06-09**)
- M5Stack Timer Camera F × 1 (fisheye, in stock; not mounted on the current rig. Preserved for the future line in `docs/fisheye_vio_roadmap.md`)
- PC × 1 (Python development)
- Input devices: joystick or keyboard × 2 people

### Communication
- Over LAN, Python ↔ StickC ↔ RoverC (I2C)
- Protocol assumed to be UDP + msgpack (subject to reconsideration)
- Synchronization accuracy is explicitly a measurement target

### Synchronization scheme (forward monocular camera + robot state)
**2026-06-09 change**: switched from a forward-stereo 2-camera setup to a forward-monocular 1-camera setup. Inter-camera pairing (time alignment of left/right frames) is no longer needed; the remaining sync problem is only **time alignment between a single camera frame and robot state (commands / telemetry)** (needed to line up camera frames with motor commands in the recorded data).
- **Policy: software timestamps + wired I2C time sync** (confirmed 2026-04-26, valid for monocular too)
- Configuration with StickC Plus2 as I2C master, putting one forward camera on the same bus as a slave alongside the RoverC STM32 (0x38) (camera: 0x40). Wiring: RoverC HAT bus (Plus2 P1 STICKIO: pin 3=G26/SCL, pin 5=G0/SDA, same layout as the plain StickC, confirmed in `stickc_plus2_schematic.pdf`) → RoverC Grove port → camera HY2.0-4P (GPIO 13=SCL, 4=SDA), reusing the existing Grove cable, no extra wiring (going monocular removes the need for a cable splice)
- RoverC's official docs only list "StickC / StickC Plus" in their compatibility table and do not mention the Plus2 name, but since the HAT pin layout matches the plain unit exactly, it is electrically compatible
- The StickC broadcast-writes the master time (`esp_timer_get_time()`) to the camera at roughly 1Hz
- The Timer Camera X records `camera_fb_t.timestamp` per frame; applying the master-time offset lets the PC side align it in time with robot state
- Sync jitter is measured and reported as an evaluation axis (consistent with the thesis "data quality" evaluation items)
- I2C bus occupancy is assumed under 5% (motor 50Hz × 5 bytes + time 1Hz × 8 bytes), low contention risk
- For the bench-test plan, see stages 3 and 5 of `docs/platform_bringup.md`
- **Rejected proposals** (history. Stereo-era studies of precise 2-camera sync, kept as a record of the design decisions):
  - GPIO trigger (rejected 2026-04-25): the OV3660 FSIN is not broken out to a pad on the Timer Camera X board, and the esp32-camera driver does not support external triggering either (detail in the Hardware investigation results section)
  - ESP-NOW time sync (rejected 2026-04-26): camera DMA coexisting with ESP-NOW is unverified on the Timer Camera X; judged that wired I2C can achieve equal-or-better accuracy at lower risk. ESP-NOW-related findings are kept as reference in the Hardware investigation results section

### Baseline Mediators (implementation targets)
1. Simple averaging
2. Weighted blend / master-slave (dominance factor)
3. (If time permits) with conflict detection, or voting

The learning-based Mediator is out of scope for this thesis (next year onward).

### Evaluation axes
- **Data quality**: sync jitter (camera ↔ robot state), dropout rate, signal-to-noise ratio (depth accuracy is out of scope with stereo retired)
- **Platform evaluation**: reproducibility, ease of setup, extensibility
- **Baseline Mediator evaluation**: task achievement, conflict frequency, dropout tolerance

### Development environment
- **Editor**: Zed (advisor's preference; no IDE)
- **ESP32 build / flash / monitor**: arduino-cli (no Arduino IDE needed)
- **Required core**: `esp32:esp32` (Espressif, board-manager URL: `https://espressif.github.io/arduino-esp32/package_esp32_index.json`)
- **Required libraries**: M5Unified (covers the whole StickC family including the Plus2); esp32-camera is bundled with `esp32:esp32`
- Board FQBNs:
  - StickC Plus2: `esp32:esp32:m5stack_stickc_plus2` (assumed to be added in Espressif core 3.x; if absent, fall back to `m5stack_stickc_plus` and let M5Unified's auto board detection handle it. Confirm in stage 1 with `arduino-cli board listall | grep stickc`)
  - StickC plain (spare): `esp32:esp32:m5stack_stickc`
  - Timer Camera X: `esp32:esp32:m5stack_timer_cam`
- Migrating to PlatformIO / ESP-IDF will be reconsidered at the thesis main-implementation stage; the bench-test stage is fully handled with arduino-cli

### Student's annual schedule (rough)

Premise: platform bring-up (RoverC teleoperation, monocular camera, time sync, performance evaluation; the old stereo-conversion stage was retired by going monocular) is completed by the advisor and handed off to the student in a verified state (`docs/platform_bringup.md`). The student focuses on the Mediator layer.

- **Apr-Jun**: Python basics + learning to use the provided platform + light Arduino (enough to read StickC sketches) + starting a rough design of the physical experiment environment
- **Jun-Aug**: Building the physical experiment environment (course, obstacles, goal, forward-monocular camera mount) + camera setup and documenting the operating procedure
- **Aug-Oct**: Implementing the multi-operator UI (PC side, keyboard × 2 as the minimum config; joystick / browser as bandwidth allows) + synchronized recording pipeline (Python, all streams output to CSV/JSONL/parquet)
- **Oct-Dec**: Implementing the baseline Mediators (averaging / dominance / voting) + running real sessions and collecting data
- **Jan-Feb**: Data-quality verification (sync jitter, dropout rate, signal-to-noise ratio) + writing the thesis

## Constraints and premises

- The student has no programming experience → ML training and complex system design are out; focus on API usage and lightweight implementation
- Completing in one year is mandatory (heavy theory premised on a PhD track is out)
- Emphasize "visible form" (footage of the real machine moving, demo-able results)
- Engineering / empirical-leaning rather than research-flavored; publication is not targeted at the thesis stage
- Trial-and-error on the ML core itself is the responsibility of later students or the advisor

## Notes on design decisions

- **At the thesis stage, novelty comes from "platform reproducibility, low cost, and ML-ready design."** Do not force methodological novelty.
- **Continuity is pulled in by the story.** "We built a foundation that can progress to a learning Mediator" draws the interest of the next student / researcher.
- **Put the upper abstraction (the general problem of Mediator design) and the long-term goal (a learning Mediator) in the thesis Intro, and focus the body on foundation-building and measurement.**
- For the student's industry prospects, this can be pitched as "design and implementation of a data-collection foundation" and "building a real-time robot system."

## State of prior work (key points)

A detailed survey is done. Key points:

- **Multi-Operator Single-Robot (MOSR) teleoperation** has been mature since 2009 (the Feth, Khademian, Sirouspour lines)
- **Comparison of aggregation methods** is partially covered by Salam et al. (AAMAS 2015) and Nguyen et al. (HRI 2025)
- **Tele-Actor / Spatial Dynamic Voting** (Goldberg, ICRA 2002) is the origin of voting-type aggregation
- **Policy blending formalism** (Dragan & Srinivasa, IJRR 2013) is the mathematical template for fusion
- **Takagi et al. (eLife 2019, Tokyo Tech)** is the decisive demonstration of 3-4 person haptic collaboration (from Japan)
- **Direct competitor**: Nguyen et al. HRI 2025 (only one confidence-sharing method → differentiate via Mediator diversity)
- **AF447 BEA report**: required in the Intro as a real example of averaging-Mediator failure
- **Empty cells**: a systematic Mediator × Domain experience map, translating social-choice-theory-based Mediators to real-time control, a low-cost demonstration platform
- **This thesis's position**: building a foundation for learning-based Mediator research. No direct competitor.

### The 5 must-reads (to hand to the student)
1. Feth et al. (2009) — MOSR terminology and the basic framing
2. Goldberg & Song (ICRA 2002) — Tele-Actor / SDV
3. Salam et al. (AAMAS 2015) — empirical comparison of aggregation methods (closest competitor)
4. Dragan & Srinivasa (IJRR 2013) — policy blending formalism
5. Takagi et al. (eLife 2019) — from Japan, multi-person haptic collaboration

Plus Losey et al. (2018) Appl. Mech. Rev. arbitration review, and the AF447 BEA report.

### Prior work per fusion method (for use in the thesis body / related-work section)

| Fusion method | Representative work | Notes |
|---|---|---|
| Arithmetic mean | Airbus sidestick / AF447 BEA report | strong as a failure case |
| Weighted continuous blend (human-human) | Khademian & Hashtrudi-Zaad (IEEE/ASME 2011, T-RO 2013) | dominance factor α |
| Weighted continuous blend (human-AI) | Dragan & Srinivasa (IJRR 2013) | policy blending formalism, the mathematical template for the Mediator |
| Subspace partitioning | Malysz & Sirouspour (IJRR 2011) | projective force mapping |
| Spatial dynamic voting | Goldberg & Song (ICRA 2002) | Tele-Actor / SDV |
| Empirical comparison of voting methods | Salam et al. (AAMAS 2015) | Leader / Average / Median |
| Haptic mechanical coupling | Takagi et al. (eLife 2019) | performance improves with 3-4 person coupling |
| Anarchy vs majority | Twitch Plays Pokemon-style studies | empirical comparison of anarchy vs democracy |
| Confidence-weighted fusion | Nguyen et al. (HRI 2025) | self-reported confidence, N=100 experiment, direct competitor |
| Token passing | da Vinci dual-console related | full-switchover type |

### General robotics trends (out of thesis scope; possibly usable as Intro background / contrast)

Surveyed (as of 2026-04). This thesis does not adopt them, but the rationale for the choices is kept:
- VLA / foundation models: out of an undergraduate's range (resource / experience shortage)
- Humanoids: hardware doesn't arrive; judged short-lived from the advisor's viewpoint
- Imitation learning / diffusion policy: heavy to implement given the student's CS experience
- Mobile manipulation: no arm, far from complex systems
- Sim-to-Real: rejected as the student's interest
- Tactile sensing: rejected as the student's interest
- Swarm: the advisor's experience gap is too large to inherit
- Edge AI / TinyML: rejected as the student's interest

## Hardware investigation results (refer to during implementation)

### M5StickC Plus2 external GPIO (operational unit)
- ESP32-PICO-V3-02, Flash 8MB + PSRAM 2MB, battery 200mAh
- Internal I2C (IMU, RTC, PMU, etc.): SCL=GPIO 22, SDA=GPIO 21 (M5Unified `In_I2C`)
- Grove port (Port A, HY2.0-4P): SCL=GPIO 33, SDA=GPIO 32 (M5Unified `Ex_I2C` standard assignment)
- HAT 8-pin header (P1 STICKIO) layout: pin1=GND, pin2=5VOUT, pin3=G26, pin4=G36, pin5=G0, pin6=BAT, pin7=3V3, pin8=5VIN (confirmed in schematic, same as the plain StickC)
- Occupied by the TFT: G15, G13, G14, G12, G5, G27 (note that the screen is larger than the plain unit, so G13 can no longer be used)
- RoverC HAT compatibility: works with pin3 G26=SCL / pin5 G0=SDA, no jumper switching

### M5StickC plain external GPIO (spare unit)
- Grove port (4-pin HY2.0): GPIO 32, GPIO 33 (+ 5V/GND) — no soldering
- Bottom 8-pin HY2.0 HAT: shares the same GPIO 32/33 + extra pins (G0, G26, G36, G25)
- Internal free GPIO: 0, 26, 36 (soldering required)
- Only GPIO 32, 33 (two lines) are externally exposed
- For the RoverC HAT, the standard on the plain unit is G26=SCL, G0=SDA

### M5Stack Timer Camera X
- Sensor: OV3660, 3MP (max 2048x1536)
- ESP32-D0WDQ6-V3 + 8MB PSRAM
- BM8563 RTC built in (for low-power sleep)
- Bottom HY2.0-4P port: SCL=GPIO 13, SDA=GPIO 4, 5V, GND
  - **GPIO 4/13 accessible without soldering**
  - Labeled I2C but repurposable as general-purpose GPIO on the ESP32 side
- GPIO occupied by the camera driver: XCLK=27, SCCB=25/23, RESET=15, data=32/35/34/5/39/18/36/19, VSYNC=22, HREF=26, PCLK=21
  - **GPIO 4/13 are not used by the camera and can be used as general-purpose inputs**

### OV3660 external trigger mode (confirmed impossible 2026-04-25)
- Checking the official Timer Camera X schematic (the M5TimerCAM PDF): **the OV3660 FSIN pin is not broken out to a pad on the board**. It cannot be pulled out even with board modification.
- The espressif/esp32-camera driver: `sensors/ov3660.c` and `ov3660_regs.h` contain zero mention of FSIN/trigger/strobe. Issue #192 ("Precise Frame Sync with 2 ESP32 Cameras") is stale with an "impossible" comment from maintainer me-no-dev.
- GPIO 4/13 on the HY2.0 port connect only to the ESP32, isolated from the OV3660.
- Conclusion: synchronization via a hardware external trigger is infeasible on this hardware configuration.
- Adopted alternative: software timestamps + ESP-NOW time sync (see the Synchronization scheme section)

### Soft-trigger accuracy (reference)
- The sensor free-runs on XCLK; `esp_camera_fb_get()` merely returns a completed frame from the queue (it does not initiate a capture)
- Acquisition mode: `CAMERA_GRAB_LATEST` always keeps the latest N frames
- (Stereo-era problem) The VSYNC phases of two cameras are independent; even calling `fb_get()` simultaneously produces skew within a frame period (~33ms at 30fps) → going monocular removes the need for inter-camera pairing
- Solution: record `camera_fb_t.timestamp` per frame (the timestamp right after VSYNC, a `struct timeval`) and do nearest-neighbor alignment with robot state in post (in the stereo era it was also used for nearest-neighbor pairing of left/right frames)

### Related projects
- **espressif/esp32-camera Issue #192** "Precise Frame Sync with 2 ESP32 Cameras" — discussion of precise 2-camera sync (stale)
- **ESPNowCam** (hpsaturn) — an ESP-NOW + WiFi-raw streamer, 1:N broadcast. **The Timer Camera X is not in the supported-board list** (centered on S3-family boards like FreenoveS3, XIAO S3, M5UnitCamS3)
- **PanoCama** (Hackaday) — dual ESP32-CAM stereo panorama + OpenCV disparity
- **Stereo Depth Perception on ESP32 S3** (Hackster) — 2 cameras on a single ESP32-S3
- No stereo / multi-camera-sync example specific to the Timer Camera X was found within the surveyed range

### ESP-NOW (time-sync protocol)
- Shares the 2.4GHz radio hardware with WiFi, but needs no AP association or IP stack. Implemented as one feature inside the WiFi driver.
- 250 bytes/packet limit, broadcast or MAC-specified unicast, typical latency from hundreds of μs to a few ms
- StickC family (including the Plus): **confirmed working** (precedents: teastainGit/RoverC-StickCPlus-ESP_NOW-Remote-Control, vkichline/BugController). The Plus2 is the same ESP32-family SoC so it can work in principle; for reference only.
- Timer Camera X: **unverified**. Outside the ESPNowCam supported list; there are reports of camera DMA × WiFi interference (espressif/esp32-camera issue #620, avoided with `fb_count=2`)
- The API flow is to bring up the radio with `WiFi.mode(WIFI_STA)` and call `esp_now_init()`. It works without an AP association.
- Constraint when used alongside an AP: the ESP-NOW channel is the same as the AP, and packets drop with modem-sleep (stated in the Espressif FAQ)
- Existing libraries: ESPNowTimeSync (jensb1, author claims ±10-50μs, no third-party measurement), ESPNowMeshClock (Hemisphere-Project), Espressif official ESP-NOW samples
- Hardware timestamps cannot be obtained: `esp_now_register_send_cb()` / `recv_cb()` are called via the WiFi task; the physical instant of radio transmit/receive is unavailable. `esp_timer_get_time()` inside the callback is the earliest obtainable point.

### camera_fb_t timestamp
- The `camera_fb_t` struct in `esp_camera.h` has a `struct timeval timestamp` field
- Value: "the time the first DMA buffer of the frame started being written" (right after VSYNC), elapsed time since boot
- To compare with robot state (the StickC-side millis clock) it must be converted to a common clock (**the master time distributed from the StickC over wired I2C**) (in the stereo era this was also used for cross-unit comparison)

## Themes ruled out (reference)

Already rejected, so no need to re-propose:
- Sim-to-Real, tactile sensing, swarm, Edge AI (rejected as the student's personal interest)
- Humanoids (judged short-lived)
- Mobile manipulation (far from complex systems)
- Full-scale learning of VLA / diffusion policy (infeasible given the student's lack of CS experience)
- World Model / Free Energy Principle / dynamical-systems analysis (theory too heavy, premised on a PhD track)
- Swarm extensions (the gap with the advisor's experience is too large to inherit)
- Embodied Cognition (viable, but with material-update cost and engagement risk)
- Physical Reservoir Computing (too hardware-leaning)
- Time-sync & latency-characteristics research on its own (too theory-leaning and plain → but folded in as a data-quality measurement)

## Still undecided

- The final number of baseline Mediators (2 or 3 or 4)
- Selection of input devices (joystick type, whether to have a browser UI)
- Whether to run human-subject experiments (within IRB scope or self-experiment only) — self-experiment + lab members is the realistic plan
- The student's Python learning resources and pace
- The dataset's publication policy (license, format, venue)
- Whether the advisor officially pursues the upper research program (the Mediator × Domain map)

## Policy for engineering work

- Answer directly first, then add detail (don't open with a long preamble to a question)
- Make proposals with awareness of whether something is in the student's scope (distinguish advisor work / student work)
- Put hardware-touching work among the priority candidates (the student's preference)
- In implementation proposals, be conscious of whether it forms "a staircase even a beginner can climb"
- Always include data quality and reproducibility among the evaluation axes (the core of the value as a collection foundation)
