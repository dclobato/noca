# BOCA Codebase Analysis: Contest and Problem Creation

## Scope

This document summarizes:

1. The overall workflow for **creating a contest**
2. The workflow for **creating a problem inside a contest**, including:
   - how **limits** are defined
   - the relation between **repetitions** and **time limit**

---

## A) Overall workflow for creating a contest

### 1. Contest creation starts in the **System** area

The entry point is the System page:
- `src/system/contest.php`

When the user chooses **new**, the page calls:
- `DBNewContest()` in `src/fcontest.php`

**Code path:**
- `src/system/contest.php:25-28`
- `src/fcontest.php:1001-1058`

---

### 2. `DBNewContest()` creates the core contest records

`DBNewContest()` does more than insert a row in `contesttable`. It creates the initial contest structure.

#### What it creates:
1. **New contest number**
   - Uses `max(contestnumber) + 1`
2. **Contest row**
   - Table: `contesttable`
3. **Initial site**
   - Calls `DBNewSite()`
   - Creates site `1` by default
4. **Default answers**
   - YES, CE, RE, TLE, PE, WA, etc.
5. **Default languages**
   - C, C++20, Java, Kotlin, Python3
6. **Fake problem 0**
   - Problem `0`, named `General`
   - Used for generic clarifications/system behavior

**References:**
- `src/fcontest.php:1008-1058`
- `src/fcontest.php:1061-1104`
- `src/fproblem.php:58-62`

---

### 3. Default values used when creating a contest

If nothing is passed, `DBNewContest()` initializes:
- `name = "Contest"`
- `startdate = now + 600 seconds`
- `duration = 300 * 60`
- `lastmileanswer = 285 * 60`
- `lastmilescore = 240 * 60`
- `penalty = 20 * 60`
- `mainsite = 1`
- `localsite = 1`
- `contestmaxfilesize = 100000` bytes
- `active = false`

**Reference:**
- `src/fcontest.php:1031-1047`

---

### 4. Initial site and admin user are created automatically

`DBNewSite()` creates the first site and also inserts the site admin user.

#### Created automatically:
- Site row in `sitetable`
- Admin user:
  - username: `admin`
  - usernumber: `1000`
  - password: `basepass` from global config
- Site start time in `sitetimetable`

**Reference:**
- `src/fcontest.php:1105-1179`

---

### 5. After creation, the contest is edited and activated

Back in `src/system/contest.php`, the new contest is loaded and the user can set:
- name, start date/time, duration, scoreboard freeze time, penalty, max file size, etc.

On submit, the page calls `DBUpdateContest($param)`. If **Activate** is pressed, all other contests are deactivated and the selected one becomes active.

**References:**
- `src/system/contest.php:39-79`
- `src/fcontest.php:761-929`

---

## B) Workflow to create a problem in a contest

### 1. Problem creation happens in the **Admin** area

Main page:
- `src/admin/problem.php`

The admin submits a problem number, short name, ZIP package, color, and autojudge settings.

**Reference:**
- `src/admin/problem.php:487-654`

---

### 2. What happens on submit

When the admin clicks **Send**:
1. The uploaded ZIP is validated.
2. `DBNewProblem()` is called.
3. ZIP package is stored in the database as a Large Object (OID).
4. Metadata (hash, name, etc.) is updated in `problemtable`.

**Reference:**
- `src/admin/problem.php:171-212`
- `src/fproblem.php:283-457`

---

### 3. Fullname and basename extraction

`DBGetFullProblemData()` unzips the package on the server side, parses `description/problem.info`, and updates the database with the `fullname` and `basename` found inside.

**Reference:**
- `src/fproblem.php:110-230`

---

## Problem package structure

A valid package contains:
- `description/problem.info` (metadata)
- `limits/` (executable scripts per language)
- `compile/`, `run/`, `compare/` (execution scripts)
- `input/`, `output/` (test data)

---

## How limits are defined

### 1. Limits are executable scripts

For each language, the package contains a script in `limits/<lang>`. BOCA executes these scripts and reads stdout.

**Reference:**
- `src/private/autojudging.php:238-260`

### 2. The four numeric lines

The script must output:
1. **time limit** (seconds)
2. **number of repetitions**
3. **memory limit** (MB)
4. **maximum output size** (KB)

**Reference:**
- `src/private/autojudging.php:312-318`
- `doc/problemexamples/problemtemplate/limits/c:1-15`

---

## Relation between repetitions and time limit

### Total Time vs Per-Run Time
- The **time limit applies to all repetitions together**.
- Repetitions are used by `safeexec` (via `-r$nruns -t$time`).

If limits say `time=4` and `repetitions=10`, the program runs up to 10 times sharing a **total budget of 4 seconds**. It is **not** 4 seconds per run.

This is primarily used for benchmarking short-running programs to ensure stability in timing.

**References:**
- `src/private/autojudging.php:379-380`
- `doc/problemexamples/problemtemplate/run/c:82-95`

---

## Built-in "Build problem package" helper

In `src/admin/buildproblem.php`, the admin can provide a single timelimit string. If the admin enters `2,5`, BOCA generates a `limits/` script that outputs `2` on the first line and `5` on the second.

**Reference:**
- `src/admin/problem.php:112-119`
