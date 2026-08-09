import streamlit as st
import pandas as pd

MAX_PLAN_RETRIES = 5


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
    selected_pair,
    department_subjects,
    retry_number=0,
):
    """
    Generate seating using the teacher-selected course pair.

    If a pair is selected, students from those two departments are placed
    on opposite seats of the same desk whenever both are available.
    Once either department runs out, the remaining students are seated
    normally. No subject comparison is used.
    """
    students = active_students.sort_values(
        by=["Department", "Roll_Number"]
    )

    queues = {
        department: students[
            students["Department"] == department
        ].to_dict("records")
        for department in active_departments
    }

    # Retry by rotating the order of non-paired departments.
    if retry_number:
        departments = list(active_departments)
        shift = retry_number % len(departments) if departments else 0
        rotated = departments[shift:] + departments[:shift]
        queues = {
            department: students[
                students["Department"] == department
            ].to_dict("records")
            for department in rotated
        }

    pair_a, pair_b = selected_pair if selected_pair else (None, None)

    seating_plan = []
    room_capacity = rows_per_room * desks_per_row * students_per_desk
    total_capacity = len(available_rooms) * room_capacity

    if len(active_students) > total_capacity:
        return pd.DataFrame()

    # Keep the selected pair at the front of the seating process.
    ordered_departments = list(active_departments)
    if pair_a in ordered_departments:
        ordered_departments.remove(pair_a)
        ordered_departments.insert(0, pair_a)
    if pair_b in ordered_departments:
        ordered_departments.remove(pair_b)
        ordered_departments.insert(1 if pair_a else 0, pair_b)

    def get_next_normal_student():
        for department in ordered_departments:
            if queues.get(department):
                return queues[department].pop(0)
        return None

    for room_number in available_rooms:
        for row in range(1, rows_per_room + 1):
            for desk in range(1, desks_per_row + 1):
                if not any(queues.get(dept) for dept in queues):
                    break

                # One seat per desk: normal sequential seating.
                if students_per_desk == 1:
                    student = get_next_normal_student()
                    if student:
                        seating_plan.append(
                            build_seating_record(
                                room_number,
                                row,
                                desk,
                                "Left",
                                student,
                                department_subjects,
                            )
                        )
                    continue

                # Two seats per desk: honor the selected pair IF both
                # departments still have students.
                student_a = None
                student_b = None

                if pair_a and pair_b and queues.get(pair_a) and queues.get(pair_b):
                    student_a = queues[pair_a].pop(0)
                    student_b = queues[pair_b].pop(0)
                else:
                    # Pairing is not compulsory. If one selected course has
                    # run out, fill the desk normally with remaining students.
                    student_a = get_next_normal_student()
                    student_b = get_next_normal_student()

                    # Avoid placing the exact same student twice.
                    if (
                        student_a is not None
                        and student_b is not None
                        and student_a["Roll_Number"] == student_b["Roll_Number"]
                    ):
                        student_b = get_next_normal_student()

                if student_a:
                    seating_plan.append(
                        build_seating_record(
                            room_number,
                            row,
                            desk,
                            "Left",
                            student_a,
                            department_subjects,
                        )
                    )

                if student_b:
                    seating_plan.append(
                        build_seating_record(
                            room_number,
                            row,
                            desk,
                            "Right",
                            student_b,
                            department_subjects,
                        )
                    )

    return pd.DataFrame(seating_plan)


def find_pairing_violations(seating_df, selected_pair):
    """
    Find desks where the teacher-selected pair could have been used but was
    not used. This is informational only; pairing is a preference/IF rule,
    not a hard constraint.
    """
    if seating_df.empty or not selected_pair:
        return []

    pair_a, pair_b = selected_pair
    violations = []

    for (room, row, desk), desk_seats in seating_df.groupby(
        ["Room", "Row", "Desk"]
    ):
        departments = set(desk_seats["Department"].tolist())

        if pair_a in departments and pair_b not in departments:
            violations.append(
                {
                    "Room": room,
                    "Row": row,
                    "Desk": desk,
                    "Selected Pair": f"{pair_a} + {pair_b}",
                    "Note": f"{pair_b} was not available for this desk.",
                }
            )
        elif pair_b in departments and pair_a not in departments:
            violations.append(
                {
                    "Room": room,
                    "Row": row,
                    "Desk": desk,
                    "Selected Pair": f"{pair_a} + {pair_b}",
                    "Note": f"{pair_a} was not available for this desk.",
                }
            )

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
        # TEACHER CONTROLLED COURSE PAIRING
        # ------------------------------------------
        st.subheader("👥 Select Course Pairing")

        if len(active_departments) >= 2:
            pair_col1, pair_col2 = st.columns(2)

            with pair_col1:
                selected_course_1 = st.selectbox(
                    "Course 1",
                    active_departments,
                    key=f"course_pair_1_{selected_date}",
                )

            remaining_course_options = [
                course
                for course in active_departments
                if course != selected_course_1
            ]

            with pair_col2:
                selected_course_2 = st.selectbox(
                    "Course 2",
                    remaining_course_options,
                    key=f"course_pair_2_{selected_date}",
                )

            selected_pair = (
                selected_course_1,
                selected_course_2,
            )

            st.info(
                f"Selected pairing: **{selected_course_1} + "
                f"{selected_course_2}**. "
                "When students from both courses are available, they "
                "will be seated on opposite sides of the same desk."
            )
        else:
            selected_pair = None
            st.info(
                "Only one course is active today, so course pairing is "
                "not required."
            )

        # ------------------------------------------
        # ACTUAL AVAILABLE ROOMS
        # ------------------------------------------
        st.subheader("🏫 Available Examination Rooms")

        st.caption(
            "Enter only rooms that are actually available for this exam. "
            "Room numbers do not need to be consecutive."
        )

        room_input = st.text_input(
            "Room Numbers",
            value="101, 102, 105, 106",
            key=f"room_input_{selected_date}",
            help="Example: 101, 102, 105, 110, 205",
        )

        available_rooms = []
        invalid_room_values = []

        for value in room_input.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                room_number = int(value)
                if room_number > 0:
                    available_rooms.append(room_number)
                else:
                    invalid_room_values.append(value)
            except ValueError:
                invalid_room_values.append(value)

        # Remove duplicate rooms while preserving order.
        available_rooms = list(dict.fromkeys(available_rooms))

        if invalid_room_values:
            st.warning(
                "Ignored invalid room numbers: "
                + ", ".join(invalid_room_values)
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

        seats_per_room = (
            rows_per_room * desks_per_row * students_per_desk
        )
        total_capacity = len(available_rooms) * seats_per_room
        total_students = len(active_students)

        cap_col1, cap_col2, cap_col3 = st.columns(3)

        with cap_col1:
            st.metric("Students", total_students)

        with cap_col2:
            st.metric("Available Seats", total_capacity)

        with cap_col3:
            if total_capacity >= total_students:
                st.success("Capacity: Sufficient")
            else:
                st.error(
                    f"Capacity: Short by "
                    f"{total_students - total_capacity} seats"
                )

        # ------------------------------------------
        # GENERATE SEATING
        # ------------------------------------------
        start_generation = st.button(
            "Generate Seating Arrangement",
            type="primary",
            key=f"generate_seating_{selected_date}",
        )

        retry_generation = st.session_state.pop(
            "retry_seating_plan", False
        )

        if start_generation or retry_generation:
            if not available_rooms:
                st.error(
                    "⚠️ Please enter at least one available examination room."
                )
                st.stop()

            if total_capacity < total_students:
                st.error(
                    f"⚠️ Insufficient seating capacity. "
                    f"{total_students} students require seats, but only "
                    f"{total_capacity} seats are available. "
                    f"Please add more examination rooms."
                )
                st.stop()

            retry_number = (
                0
                if start_generation
                else st.session_state.get("plan_retry_count", 0)
            )

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
                        selected_pair=selected_pair,
                        department_subjects=department_subjects,
                        retry_number=retry_number,
                    )
                )

                st.session_state["selected_pair"] = selected_pair

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
            selected_pair = st.session_state.get("selected_pair")
            pairing_notes = find_pairing_violations(
                candidate_plan,
                selected_pair,
            )

            # Pairing is a preference, not a hard conflict. Therefore,
            # show the teacher a confirmation only when a selected pair
            # could not be maintained for all desks because one course
            # became unavailable.
            if pairing_notes and selected_pair:
                @st.dialog("ℹ️ Seating Pairing Notice")
                def seating_pairing_dialog():
                    pair_text = (
                        f"{selected_pair[0]} + {selected_pair[1]}"
                    )

                    st.write(
                        f"You selected **{pair_text}** as the preferred "
                        "course pairing."
                    )

                    st.write(
                        "The pairing was used whenever students from "
                        "both courses were available. After one course "
                        "ran out, the remaining students were seated "
                        "normally."
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

        seating_csv = st.session_state["seating_df"].to_csv(
            index=False
        ).encode("utf-8")

        if st.session_state.get("confirm_plan_download", False):
            st.warning(
                "⚠️ The seating plan is ready to download."
            )

            download_col, cancel_col = st.columns(2)

            with download_col:
                st.download_button(
                    label="⬇️ Download Seating Plan (CSV)",
                    data=seating_csv,
                    file_name=f"seating_plan_{selected_date}.csv",
                    mime="text/csv",
                    key="confirm_plan_download_button",
                )

            with cancel_col:
                if st.button(
                    "No, cancel",
                    key="cancel_plan_download",
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
