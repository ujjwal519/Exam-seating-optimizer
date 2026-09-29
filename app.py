import streamlit as st
import pandas as pd

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

MAX_PLAN_RETRIES = 5


def parse_available_rooms(uploaded_file):
    """Read available room numbers from a CSV or PDF upload."""
    if uploaded_file is None:
        return [], []

    file_name = uploaded_file.name.lower()
    rooms = []
    invalid_values = []

    try:
        if file_name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
            df.columns = df.columns.str.strip()

            # Accept common room-number column names.
            possible_columns = [
                "Room", "Room_Number", "Room Number", "RoomNo",
                "Room_No", "room", "room_number"
            ]
            room_column = next(
                (col for col in possible_columns if col in df.columns), None
            )

            if room_column is None:
                # Also allow a single-column CSV without a header.
                uploaded_file.seek(0)
                raw_df = pd.read_csv(uploaded_file, header=None)
                if raw_df.shape[1] == 1:
                    values = raw_df.iloc[:, 0].tolist()
                else:
                    return [], [
                        "CSV must contain a room-number column such as "
                        "'Room' or 'Room_Number'."
                    ]
            else:
                values = df[room_column].tolist()

        elif file_name.endswith(".pdf"):
            if PdfReader is None:
                return [], [
                    "PDF support requires the 'pypdf' package. "
                    "Install it with: pip install pypdf"
                ]

            uploaded_file.seek(0)
            reader = PdfReader(uploaded_file)
            pdf_text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )

            # Extract standalone room numbers from PDF text.
            import re
            values = re.findall(r"(?<!\d)\d{1,5}(?!\d)", pdf_text)

        else:
            return [], ["Unsupported file type. Please upload CSV or PDF."]

        for value in values:
            value_str = str(value).strip()

            if not value_str or value_str.lower() in {"nan", "none", "room"}:
                continue

            # Handle values such as "Room 101" / "101.0".
            import re
            match = re.search(r"(?<!\d)(\d{1,5})(?:\.0)?(?!\d)", value_str)
            if match:
                room_number = int(match.group(1))
                if room_number > 0:
                    rooms.append(room_number)
                else:
                    invalid_values.append(value_str)
            else:
                invalid_values.append(value_str)

        # Remove duplicates while preserving source order.
        rooms = list(dict.fromkeys(rooms))

    except Exception as exc:
        return [], [f"Could not read the room file: {exc}"]

    return rooms, invalid_values



def build_seating_record(room_number, row, desk, seat, student, department_subjects):
    subjects = department_subjects.get(student["Department"], set())
    return {
        "Room": f"Room {room_number}",
        "Row": row,
        "Desk": desk,
        "Seat": seat,
        "Roll_Number": student["Roll_Number"],
        "Department": student["Department"],
        "Subject": ", ".join(sorted(subjects)),
    }


def generate_pair_seating_plan(
    active_students,
    active_departments,
    available_rooms,
    rows_per_room,
    desks_per_row,
    students_per_desk,
    course_pairs,
    single_course_departments,
    department_subjects,
    retry_number=0,
):
    """
    Generate seating according to the teacher's explicit course configuration.

    - Every pair in course_pairs gets paired seating: one student from each
      course on opposite sides of a desk.
    - Every course in single_course_departments gets single seating:
      one student per desk.
    - Multiple teacher-selected pairs are supported.
    - Rooms are never shared between different seating groups.
    """
    course_pairs = course_pairs or []
    single_course_departments = single_course_departments or []

    students = active_students.sort_values(by=["Department", "Roll_Number"])

    groups = []
    paired_courses = set()

    for pair in course_pairs:
        if len(pair) != 2:
            continue
        pair_a, pair_b = pair
        groups.append({
            "type": "Paired",
            "departments": [pair_a, pair_b],
        })
        paired_courses.update([pair_a, pair_b])

    for department in single_course_departments:
        if department not in paired_courses:
            groups.append({
                "type": "Single",
                "departments": [department],
            })

    seating_plan = []
    room_index = 0

    def room_capacity_for(group_type):
        if group_type == "Paired" and students_per_desk == 2:
            return rows_per_room * desks_per_row * 2
        return rows_per_room * desks_per_row

    def build_group_seating(group, rooms):
        departments = group["departments"]
        group_students = students[
            students["Department"].isin(departments)
        ].copy()

        if group_students.empty:
            return []

        if retry_number:
            group_students = group_students.sort_values(
                by=["Department", "Roll_Number"]
            )

        records = []

        if group["type"] == "Paired" and students_per_desk == 2:
            queues = {
                department: group_students[
                    group_students["Department"] == department
                ].to_dict("records")
                for department in departments
            }

            for room_number in rooms:
                for row in range(1, rows_per_room + 1):
                    for desk in range(1, desks_per_row + 1):
                        if not any(queues.get(d) for d in queues):
                            break

                        left = queues.get(departments[0], [])
                        right = queues.get(departments[1], [])

                        student_a = left.pop(0) if left else None
                        student_b = right.pop(0) if right else None

                        if student_a is None and student_b is None:
                            continue

                        if student_a:
                            record = build_seating_record(
                                room_number, row, desk, "Left",
                                student_a, department_subjects
                            )
                            record["Plan_Type"] = "Paired"
                            record["Course_Group"] = (
                                f"{departments[0]} + {departments[1]}"
                            )
                            records.append(record)

                        if student_b:
                            record = build_seating_record(
                                room_number, row, desk, "Right",
                                student_b, department_subjects
                            )
                            record["Plan_Type"] = "Paired"
                            record["Course_Group"] = (
                                f"{departments[0]} + {departments[1]}"
                            )
                            records.append(record)

        else:
            queue = group_students.to_dict("records")

            for room_number in rooms:
                for row in range(1, rows_per_room + 1):
                    for desk in range(1, desks_per_row + 1):
                        if not queue:
                            break

                        student = queue.pop(0)
                        record = build_seating_record(
                            room_number, row, desk, "Left",
                            student, department_subjects
                        )
                        record["Plan_Type"] = "Single"
                        record["Course_Group"] = departments[0]
                        records.append(record)

        if len(records) != len(group_students):
            return None

        return records

    # Allocate rooms sequentially. A room is owned by exactly one group.
    for group in groups:
        group_students_count = len(
            students[students["Department"].isin(group["departments"])]
        )
        capacity_per_room = room_capacity_for(group["type"])

        rooms_needed = (
            (group_students_count + capacity_per_room - 1)
            // capacity_per_room
        )

        group_rooms = available_rooms[
            room_index:room_index + rooms_needed
        ]

        if len(group_rooms) < rooms_needed:
            return pd.DataFrame()

        group_records = build_group_seating(group, group_rooms)
        if group_records is None:
            return pd.DataFrame()

        seating_plan.extend(group_records)
        room_index += rooms_needed

    if seating_plan:
        result = pd.DataFrame(seating_plan)

        # Final safety check: no room may contain more than one seating type
        # or more than one course group.
        room_types = result.groupby("Room")["Plan_Type"].nunique()
        room_groups = result.groupby("Room")["Course_Group"].nunique()

        if (room_types > 1).any() or (room_groups > 1).any():
            return pd.DataFrame()

        return result

    return pd.DataFrame()


def find_pairing_violations(seating_df, course_pairs):
    """
    Find desks where a teacher-selected pair could not be maintained because
    one course ran out of students. This is informational only.
    """
    if seating_df.empty or not course_pairs:
        return []

    violations = []

    for pair_a, pair_b in course_pairs:
        pair_text = f"{pair_a} + {pair_b}"

        pair_df = seating_df[
            seating_df["Course_Group"] == pair_text
        ]

        if pair_df.empty:
            continue

        for (room, row, desk), desk_seats in pair_df.groupby(
            ["Room", "Row", "Desk"]
        ):
            departments = set(desk_seats["Department"].tolist())

            if pair_a in departments and pair_b not in departments:
                violations.append({
                    "Room": room,
                    "Row": row,
                    "Desk": desk,
                    "Selected Pair": pair_text,
                    "Note": f"{pair_b} was not available for this desk.",
                })
            elif pair_b in departments and pair_a not in departments:
                violations.append({
                    "Room": room,
                    "Row": row,
                    "Desk": desk,
                    "Selected Pair": pair_text,
                    "Note": f"{pair_a} was not available for this desk.",
                })

    return violations


# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Exam Seating & Invigilation Optimizer",
    layout="wide",
)

st.title("🎓 Exam Seating & Invigilation Optimizer")
st.markdown(
    "Teacher-controlled course pairing and practical examination-room seating."
)

# ==========================================
# SIDEBAR: DATA UPLOADS
# ==========================================
st.sidebar.header("📂 Data Inputs")
date_sheet_file = st.sidebar.file_uploader(
    "1. Upload Date Sheet (CSV)", type=["csv"]
)
roster_file = st.sidebar.file_uploader(
    "2. Upload Student Roster (CSV)", type=["csv"]
)
faculty_file = st.sidebar.file_uploader(
    "3. Upload Faculty List (CSV)", type=["csv"]
)

# ==========================================
# PILLAR 1: DATA INGESTION & FILTERING
# ==========================================
st.header("📊 Pillar 1: Exam Schedule & Active Departments")

if date_sheet_file and roster_file:
    date_sheet_df = pd.read_csv(date_sheet_file)
    roster_df = pd.read_csv(roster_file)

    date_sheet_df.columns = date_sheet_df.columns.str.strip()
    roster_df.columns = roster_df.columns.str.strip()

    required_date_sheet_columns = {"Date", "Department", "Subject"}
    required_roster_columns = {"Roll_Number", "Department"}

    missing_date_sheet_columns = (
        required_date_sheet_columns - set(date_sheet_df.columns)
    )
    missing_roster_columns = required_roster_columns - set(roster_df.columns)

    if missing_date_sheet_columns or missing_roster_columns:
        if missing_date_sheet_columns:
            st.error(
                "⚠️ The Date Sheet CSV is invalid. Missing columns: "
                + ", ".join(sorted(missing_date_sheet_columns))
            )

        if missing_roster_columns:
            st.error(
                "⚠️ The Student Roster CSV is invalid. Missing columns: "
                + ", ".join(sorted(missing_roster_columns))
                + "."
            )

        st.stop()

    unique_dates = date_sheet_df["Date"].dropna().unique()
    selected_date = st.selectbox("Select Exam Date:", unique_dates)

    active_exams = date_sheet_df[
        date_sheet_df["Date"] == selected_date
    ].copy()

    active_departments = (
        active_exams["Department"].dropna().unique().tolist()
    )

    department_subjects = (
        active_exams.groupby("Department")["Subject"]
        .agg(lambda subjects: set(subjects.dropna().astype(str)))
        .to_dict()
    )

    st.success(
        f"Active Departments on {selected_date}: "
        + ", ".join(active_departments)
    )
    st.dataframe(active_exams, hide_index=True)

    # ==========================================
    # PILLAR 2: SEATING ALLOCATION
    # ==========================================
    st.markdown("---")
    st.header("🪑 Pillar 2: Examination Seating Engine")

    active_students = roster_df[
        roster_df["Department"].isin(active_departments)
    ].copy()

    if not active_students.empty:

        # ------------------------------------------
        # TEACHER-CONTROLLED COURSE SEATING CONFIGURATION
        # ------------------------------------------
        st.subheader("👥 Course Seating Configuration")
        st.caption(
            "You decide exactly which courses are paired and which courses "
            "are single. You can create multiple pairs."
        )

        course_pairs = []
        single_course_departments = []
        course_pair_choice = {}

        # When the teacher selects a course to pair with another course,
        # automatically select the reciprocal pairing as well. This means:
        # BBA -> Pair with BCA  ==>  BCA -> Pair with BBA automatically.
        def sync_course_pair(course, selected_date, active_courses):
            key = f"course_seating_{selected_date}_{course}"
            previous_key = f"course_previous_choice_{selected_date}_{course}"
            choice = st.session_state.get(key, "Single")
            previous_choice = st.session_state.get(previous_key, "Single")

            # First remove this course's OLD reciprocal pairing, if it had one.
            # Example: BBA was paired with BCA, then the teacher changes BBA
            # to pair with MCA. BCA must automatically become Single.
            if previous_choice.startswith("Pair with "):
                old_partner = previous_choice.replace("Pair with ", "", 1)
                old_partner_key = f"course_seating_{selected_date}_{old_partner}"
                if st.session_state.get(old_partner_key) == f"Pair with {course}":
                    st.session_state[old_partner_key] = "Single"

            if choice.startswith("Pair with "):
                partner = choice.replace("Pair with ", "", 1)
                partner_key = f"course_seating_{selected_date}_{partner}"

                # If the new partner already belongs to another pair, break
                # that old pair so every course can belong to only one pair.
                partner_old_choice = st.session_state.get(partner_key, "Single")
                if partner_old_choice.startswith("Pair with "):
                    partner_old_partner = partner_old_choice.replace(
                        "Pair with ", "", 1
                    )
                    if partner_old_partner != course:
                        other_key = (
                            f"course_seating_{selected_date}_{partner_old_partner}"
                        )
                        if st.session_state.get(other_key) == f"Pair with {partner}":
                            st.session_state[other_key] = "Single"

                # Automatically select the reciprocal pairing.
                st.session_state[partner_key] = f"Pair with {course}"

            st.session_state[previous_key] = choice

        for course in active_departments:
            other_courses = [
                c for c in active_departments
                if c != course
            ]

            options = ["Single"] + [
                f"Pair with {other}" for other in other_courses
            ]
            key = f"course_seating_{selected_date}_{course}"

            if key not in st.session_state:
                st.session_state[key] = "Single"

            course_pair_choice[course] = st.selectbox(
                f"Seating for **{course}**",
                options,
                key=key,
                on_change=sync_course_pair,
                args=(course, selected_date, active_departments),
            )

        # Convert the now-synchronised teacher choices into unique pairs.
        # No manual reciprocal selection is required anymore.
        configuration_errors = []
        for course in active_departments:
            choice = course_pair_choice.get(course, "Single")

            if choice == "Single":
                single_course_departments.append(course)
                continue

            partner = choice.replace("Pair with ", "", 1)
            if partner not in active_departments or partner == course:
                configuration_errors.append(
                    f"{course} has an invalid pairing selection."
                )
                continue

            # This should normally be automatic because of the callback, but
            # validate it as a safety check before generating the plan.
            partner_choice = course_pair_choice.get(partner, "Single")
            if partner_choice != f"Pair with {course}":
                configuration_errors.append(
                    f"{course} is set to pair with {partner}, but {partner} "
                    f"is not set to pair with {course}."
                )
                continue

            pair = tuple(sorted((course, partner)))
            if pair not in course_pairs:
                course_pairs.append(pair)


        # Courses involved in a pair cannot also be single.
        paired_courses = {
            course
            for pair in course_pairs
            for course in pair
        }
        single_course_departments = [
            course
            for course in active_departments
            if course not in paired_courses
            and course_pair_choice.get(course) == "Single"
        ]

        if course_pairs:
            st.success(
                "✅ Courses you chose to pair: "
                + ", ".join(f"{a} + {b}" for a, b in course_pairs)
            )
        if single_course_departments:
            st.info(
                "📌 Courses you chose as Single: "
                + ", ".join(single_course_departments)
            )
        if not course_pairs and not single_course_departments:
            st.warning("Please configure at least one course.")

        # Backward-compatible summary used by the rest of the app.
        selected_pair = course_pairs[0] if course_pairs else None

        current_course_config = (
            tuple(sorted(tuple(sorted(pair)) for pair in course_pairs)),
            tuple(sorted(single_course_departments)),
            tuple(sorted(course_pair_choice.items())),
        )
        course_config_key = f"course_config_{selected_date}"

        if st.session_state.get(course_config_key) != current_course_config:
            st.session_state.pop("candidate_seating_df", None)
            st.session_state.pop("seating_df", None)
            st.session_state.pop("retry_seating_plan", None)
            st.session_state["plan_retry_count"] = 0
            st.session_state["confirm_plan_download"] = False
            st.session_state[course_config_key] = current_course_config

        # ACTUAL AVAILABLE ROOMS
        # ------------------------------------------
        st.subheader("🏫 Available Examination Rooms")

        st.caption(
            "Upload a CSV or PDF containing the rooms that are actually "
            "available for this exam. Room numbers do not need to be consecutive."
        )

        room_file = st.file_uploader(
            "Upload Available Rooms (CSV or PDF)",
            type=["csv", "pdf"],
            key=f"room_file_{selected_date}",
            help=(
                "CSV: use a column named Room, Room_Number, Room Number, "
                "RoomNo, or Room_No. PDF: room numbers can appear as text, "
                "for example 101, 102, 105."
            ),
        )

        available_rooms = []
        invalid_room_values = []

        if room_file is not None:
            available_rooms, invalid_room_values = parse_available_rooms(
                room_file
            )

            if available_rooms:
                st.success(
                    f"✅ Loaded {len(available_rooms)} available room(s): "
                    + ", ".join(map(str, available_rooms))
                )
            else:
                st.error(
                    "⚠️ No valid room numbers were found in the uploaded file."
                )

            if invalid_room_values:
                st.warning(
                    "Ignored invalid room values: "
                    + ", ".join(invalid_room_values[:20])
                    + (" ..." if len(invalid_room_values) > 20 else "")
                )
        else:
            st.info(
                "Please upload the available-room CSV/PDF before generating "
                "the seating plan."
            )

        # ------------------------------------------
        # CLASSROOM CONFIGURATION
        # ------------------------------------------
        st.subheader("⚙️ Classroom Configuration")

        col1, col2, col3 = st.columns(3)

        with col1:
            rows_per_room = st.number_input(
                "Rows per Room",
                min_value=1,
                value=4,
                step=1,
                key=f"rows_{selected_date}",
            )

        with col2:
            desks_per_row = st.number_input(
                "Desks per Row",
                min_value=1,
                value=6,
                step=1,
                key=f"desks_{selected_date}",
            )

        with col3:
            students_per_desk = st.number_input(
                "Students per Desk",
                min_value=1,
                max_value=2,
                value=2,
                step=1,
                key=f"students_per_desk_{selected_date}",
            )

        # Pairing requires two students per desk.
        # This check MUST happen after students_per_desk is created.
        if course_pairs and students_per_desk != 2:
            configuration_errors.append(
                "At least one course pair is selected, so Students per Desk "
                "must be 2."
            )

        seats_per_room = (
            rows_per_room * desks_per_row * students_per_desk
        )
        total_students = len(active_students)

        # Calculate the actual number of rooms required from the
        # teacher-selected pairs and singles.
        pair_capacity_per_room = rows_per_room * desks_per_row * 2
        single_capacity_per_room = rows_per_room * desks_per_row

        pair_rooms_required = 0
        for pair in course_pairs:
            count = len(
                active_students[
                    active_students["Department"].isin(pair)
                ]
            )
            pair_rooms_required += (
                (count + pair_capacity_per_room - 1)
                // pair_capacity_per_room
                if count
                else 0
            )

        single_rooms_required = 0
        for department in single_course_departments:
            count = len(
                active_students[
                    active_students["Department"] == department
                ]
            )
            single_rooms_required += (
                (count + single_capacity_per_room - 1)
                // single_capacity_per_room
                if count
                else 0
            )

        required_rooms = pair_rooms_required + single_rooms_required
        total_capacity = (
            pair_rooms_required * pair_capacity_per_room
            + single_rooms_required * single_capacity_per_room
        )

        cap_col1, cap_col2, cap_col3 = st.columns(3)

        with cap_col1:
            st.metric("Students", total_students)

        with cap_col2:
            st.metric("Rooms Required", required_rooms)

        with cap_col3:
            if len(available_rooms) >= required_rooms:
                st.success(
                    f"Rooms Available: {len(available_rooms)}"
                )
            else:
                st.error(
                    f"Rooms Short by {required_rooms - len(available_rooms)}"
                )

        # ------------------------------------------
        # GENERATE / RETRY SEATING PLAN
        # ------------------------------------------
        if configuration_errors:
            st.error(
                "⚠️ Fix the course pairing configuration above before "
                "generating the seating plan."
            )
            st.stop()

        if len(available_rooms) < required_rooms:
            st.error(
                f"⚠️ Insufficient examination rooms. "
                f"This seating mode requires {required_rooms} room(s), "
                f"but only {len(available_rooms)} room(s) were uploaded. "
                f"Please upload a room file containing more available rooms."
            )
            st.stop()

        start_generation = st.button(
            "🎯 Generate Seating Plan",
            key=f"generate_seating_{selected_date}",
            type="primary",
        )

        retry_generation = st.session_state.get("retry_seating_plan", False)

        if start_generation or retry_generation:
            retry_number = (
                0
                if start_generation
                else st.session_state.get("plan_retry_count", 0)
            )
            st.session_state["retry_seating_plan"] = False

            with st.spinner("Calculating seating layout..."):
                st.session_state.pop("seating_df", None)
                st.session_state["confirm_plan_download"] = False
                st.session_state["plan_retry_count"] = retry_number

                st.session_state["candidate_seating_df"] = (
                    generate_pair_seating_plan(
                        active_students=active_students,
                        active_departments=active_departments,
                        available_rooms=available_rooms,
                        rows_per_room=rows_per_room,
                        desks_per_row=desks_per_row,
                        students_per_desk=students_per_desk,
                        course_pairs=course_pairs,
                        single_course_departments=single_course_departments,
                        department_subjects=department_subjects,
                        retry_number=retry_number,
                    )
                )

                st.session_state["course_pairs"] = course_pairs

    # ==========================================
    # SEATING PLAN RESULT
    # ==========================================
    if "candidate_seating_df" in st.session_state:
        candidate_plan = st.session_state["candidate_seating_df"]

        if candidate_plan.empty:
            st.error(
                "⚠️ Seating plan could not be generated. "
                "Please check your room capacity."
            )
        elif "seating_df" not in st.session_state:
            course_pairs = st.session_state.get("course_pairs", [])
            pairing_notes = find_pairing_violations(
                candidate_plan,
                course_pairs,
            )

            # Pairing is a preference, not a hard conflict. Therefore,
            # show the teacher a confirmation only when a selected pair
            # could not be maintained for all desks because one course
            # became unavailable.
            if pairing_notes and course_pairs:
                @st.dialog("ℹ️ Seating Pairing Notice")
                def seating_pairing_dialog():
                    pair_text = ", ".join(
                        f"{a} + {b}" for a, b in course_pairs
                    )

                    st.write(
                        f"You selected these course pairs: **{pair_text}**."
                    )

                    st.write(
                        "Each selected pair is kept in its own rooms. "
                        "If one course in a pair has fewer students, the "
                        "remaining students from that course are seated "
                        "alone in unused seats."
                    )

                    st.dataframe(
                        pd.DataFrame(pairing_notes),
                        hide_index=True,
                        width="stretch",
                    )

                    continue_col, retry_col = st.columns(2)

                    with continue_col:
                        if st.button(
                            "Continue with this Plan",
                            key="continue_pairing_plan",
                        ):
                            st.session_state["seating_df"] = candidate_plan
                            st.session_state[
                                "confirm_plan_download"
                            ] = True
                            st.rerun()

                    with retry_col:
                        if st.session_state.get(
                            "plan_retry_count", 0
                        ) < MAX_PLAN_RETRIES:
                            if st.button(
                                "Generate Another Plan",
                                key="retry_pairing_plan",
                            ):
                                st.session_state.pop(
                                    "candidate_seating_df",
                                    None,
                                )
                                st.session_state[
                                    "plan_retry_count"
                                ] = (
                                    st.session_state.get(
                                        "plan_retry_count", 0
                                    )
                                    + 1
                                )
                                st.session_state[
                                    "retry_seating_plan"
                                ] = True
                                st.rerun()
                        else:
                            st.warning(
                                f"Retry limit of {MAX_PLAN_RETRIES} "
                                "reached."
                            )

                seating_pairing_dialog()
            else:
                st.session_state["seating_df"] = candidate_plan
                st.session_state["confirm_plan_download"] = True

    # ==========================================
    # DISPLAY ACCEPTED PLAN
    # ==========================================
    if "seating_df" in st.session_state:
        st.success("✅ Seating generated successfully!")
        st.dataframe(
            st.session_state["seating_df"],
            hide_index=True,
            width="stretch",
        )

        # Show exactly which rooms belong to paired vs single-course seating.
        if "Plan_Type" in st.session_state["seating_df"].columns:
            st.markdown("### 🏫 Room Allocation Summary")
            room_summary = (
                st.session_state["seating_df"]
                .groupby(["Room", "Plan_Type", "Course_Group"], sort=False)
                .size()
                .reset_index(name="Students")
            )
            st.dataframe(
                room_summary,
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "🔒 Room protection is active: a room assigned to a Paired "
                "course group is never reused for a Single course group."
            )

        seating_df_for_download = st.session_state["seating_df"].copy()
        seating_csv = seating_df_for_download.to_csv(
            index=False
        ).encode("utf-8")

        if st.session_state.get("confirm_plan_download", False):
            st.warning(
                "⚠️ The seating plan is ready to download."
            )

            # --------------------------------------------------
            # DOWNLOAD OPTIONS
            # --------------------------------------------------
            st.markdown("### 📥 Download Options")

            download_col, range_col, cancel_col = st.columns([1, 1, 1])

            with download_col:
                st.download_button(
                    label="⬇️ Download Whole Seating Plan",
                    data=seating_csv,
                    file_name=f"seating_plan_{selected_date}.csv",
                    mime="text/csv",
                    key="confirm_plan_download_button",
                    use_container_width=True,
                )

            with range_col:
                # Let the teacher choose exactly which course's room-wise
                # roll-number ranges should be downloaded.
                available_courses_for_range = sorted(
                    seating_df_for_download["Department"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )

                selected_range_course = st.selectbox(
                    "Course for Roll No. Range",
                    available_courses_for_range,
                    key=f"range_course_{selected_date}",
                    help=(
                        "Choose a course to see the starting and ending roll "
                        "number seated in each room."
                    ),
                )

                course_range_df = seating_df_for_download[
                    seating_df_for_download["Department"].astype(str)
                    == str(selected_range_course)
                ].copy()

                def roll_sort_key(value):
                    value_str = str(value).strip()
                    try:
                        return (0, int(float(value_str)))
                    except (ValueError, TypeError):
                        return (1, value_str)

                range_rows = []
                for room, room_students in course_range_df.groupby(
                    "Room", sort=False
                ):
                    roll_numbers = sorted(
                        room_students["Roll_Number"]
                        .dropna()
                        .astype(str)
                        .tolist(),
                        key=roll_sort_key,
                    )

                    if not roll_numbers:
                        continue

                    range_rows.append({
                        "Course": selected_range_course,
                        "Room": room,
                        "Starting Roll No": roll_numbers[0],
                        "Ending Roll No": roll_numbers[-1],
                        "Roll No Range": (
                            f"{roll_numbers[0]} to {roll_numbers[-1]}"
                        ),
                        "Students in Room": len(roll_numbers),
                    })

                course_range_download_df = pd.DataFrame(range_rows)

                if not course_range_download_df.empty:
                    st.dataframe(
                        course_range_download_df,
                        hide_index=True,
                        width="stretch",
                    )

                    course_range_csv = course_range_download_df.to_csv(
                        index=False
                    ).encode("utf-8")

                    st.download_button(
                        label=f"⬇️ Download {selected_range_course} Roll Ranges",
                        data=course_range_csv,
                        file_name=(
                            f"{selected_range_course}_roll_ranges_"
                            f"{selected_date}.csv"
                        ),
                        mime="text/csv",
                        key=f"download_range_{selected_date}_{selected_range_course}",
                        use_container_width=True,
                    )
                else:
                    st.info(
                        f"No seating information found for {selected_range_course}."
                    )

            with cancel_col:
                if st.button(
                    "No, cancel",
                    key="cancel_plan_download",
                    use_container_width=True,
                ):
                    st.session_state["confirm_plan_download"] = False

    elif active_students.empty:
        st.warning(
            "No students found in the roster for today's active exams."
        )

    # ==========================================
    # PILLAR 3: STRANGER PROTOCOL
    # ==========================================
    st.markdown("---")
    st.header("🕵️ Pillar 3: Stranger Protocol")

    if faculty_file is not None:
        faculty_df = pd.read_csv(faculty_file)
        faculty_df.columns = faculty_df.columns.str.strip()

        required_faculty_columns = {
            "Emp_ID",
            "Name",
            "Department",
            "Designation",
        }

        missing_faculty_columns = (
            required_faculty_columns - set(faculty_df.columns)
        )

        if missing_faculty_columns:
            st.error(
                "⚠️ The Faculty List CSV is invalid. Missing columns: "
                + ", ".join(sorted(missing_faculty_columns))
            )
            st.stop()

        st.subheader("🛡️ Stranger Protocol Active")
        st.info(
            f"Active Exams Today: {', '.join(active_departments)}"
        )

        eligible_faculty = faculty_df[
            ~faculty_df["Department"].isin(active_departments)
        ].copy()

        if not eligible_faculty.empty:
            st.success(
                f"Found {len(eligible_faculty)} eligible invigilators "
                "with zero conflict of interest."
            )

            st.markdown("### 📋 Allocate Invigilators")

            invigilators_per_room = st.number_input(
                "Target Invigilators per Room",
                min_value=1,
                value=2,
                step=1,
                key=f"invigilators_per_room_{selected_date}",
            )

            st.write(
                "Select the teachers and finalize the plan. "
                "Room numbers come from the seating plan."
            )

            state_key = f"faculty_editor_{selected_date}"

            if state_key not in st.session_state:
                eligible_faculty.insert(0, "Select", False)
                eligible_faculty["Assigned_Room"] = ""
                st.session_state[state_key] = eligible_faculty

            edited_faculty = st.data_editor(
                st.session_state[state_key],
                hide_index=True,
                key=f"faculty_data_editor_{selected_date}",
                column_config={
                    "Select": st.column_config.CheckboxColumn(
                        "Select",
                        default=False,
                    ),
                    "Assigned_Room": st.column_config.TextColumn(
                        "Assigned Room",
                        help=(
                            "Assigned automatically when the plan "
                            "is finalized"
                        ),
                    ),
                },
                disabled=[
                    "Emp_ID",
                    "Name",
                    "Department",
                    "Designation",
                    "Assigned_Room",
                ],
            )

            st.session_state[state_key] = edited_faculty

            allocation_key = (
                f"invigilation_allocation_{selected_date}"
            )
            finalized_selection_key = (
                f"finalized_faculty_selection_{selected_date}"
            )

            selected_ids = tuple(
                edited_faculty.loc[
                    edited_faculty["Select"], "Emp_ID"
                ]
            )

            if (
                st.session_state.get(finalized_selection_key)
                != selected_ids
            ):
                st.session_state.pop(allocation_key, None)

            if st.button(
                "Finalize Invigilation Plan",
                key=f"finalize_invigilation_{selected_date}",
            ):
                final_allocation = edited_faculty[
                    edited_faculty["Select"] == True
                ]

                if final_allocation.empty:
                    st.warning(
                        "⚠️ You haven't selected any faculty members yet."
                    )

                elif "seating_df" not in st.session_state:
                    st.warning(
                        "⚠️ Generate and accept a seating plan before "
                        "assigning invigilators to rooms."
                    )

                else:
                    room_numbers = (
                        st.session_state["seating_df"]["Room"]
                        .drop_duplicates()
                        .tolist()
                    )

                    available_assignments = [
                        room
                        for room in room_numbers
                        for _ in range(invigilators_per_room)
                    ]

                    selected_indexes = final_allocation.index.tolist()

                    edited_faculty["Assigned_Room"] = ""

                    for faculty_index, room in zip(
                        selected_indexes,
                        available_assignments,
                    ):
                        edited_faculty.loc[
                            faculty_index, "Assigned_Room"
                        ] = room

                    st.session_state[state_key] = edited_faculty

                    final_allocation = edited_faculty[
                        edited_faculty["Select"] == True
                    ]

                    allocation_columns = [
                        "Assigned_Room",
                        "Emp_ID",
                        "Name",
                        "Department",
                        "Designation",
                    ]

                    st.session_state[allocation_key] = (
                        final_allocation[
                            final_allocation["Assigned_Room"] != ""
                        ][allocation_columns].copy()
                    )

                    st.session_state[
                        finalized_selection_key
                    ] = selected_ids

                    if len(selected_indexes) < len(
                        available_assignments
                    ):
                        st.warning(
                            f"⚠️ Assigned {len(selected_indexes)} "
                            "selected faculty members. "
                            f"{len(available_assignments) - len(selected_indexes)} "
                            "room assignment(s) still need invigilators."
                        )

                    elif len(selected_indexes) > len(
                        available_assignments
                    ):
                        st.warning(
                            f"⚠️ Only {len(available_assignments)} "
                            "assignments fit the current target. "
                            "Extra selected faculty have not been "
                            "assigned a room."
                        )

                    st.success(
                        "✅ Invigilation Plan Finalized!"
                    )
                    st.dataframe(
                        final_allocation[allocation_columns],
                        hide_index=True,
                    )

            if allocation_key in st.session_state:
                allocation_csv = (
                    st.session_state[allocation_key]
                    .to_csv(index=False)
                    .encode("utf-8")
                )

                st.download_button(
                    label="⬇️ Download Faculty Room Allocation (CSV)",
                    data=allocation_csv,
                    file_name=(
                        f"faculty_room_allocation_{selected_date}.csv"
                    ),
                    mime="text/csv",
                    key=f"download_faculty_allocation_{selected_date}",
                )

        else:
            st.error(
                "⚠️ CRITICAL ALERT: No neutral faculty available "
                "for invigilation today! Manual scheduling override "
                "required."
            )

    else:
        st.warning(
            "⚠️ Please upload the Faculty CSV in the sidebar to "
            "proceed with invigilator assignment."
        )

else:
    st.info(
        "👋 Welcome! Please upload your CSV files in the sidebar "
        "to begin."
    )
 
