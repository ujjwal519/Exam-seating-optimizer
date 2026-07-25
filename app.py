import streamlit as st
import pandas as pd
import math

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Exam Seating & Invigilation Optimizer", layout="wide")
st.title("🎓 Exam Seating & Invigilation Optimizer")
st.markdown("Automated checkerboard seating and Stranger Protocol invigilation matching.")

# ==========================================
# SIDEBAR: DATA UPLOADS
# ==========================================
st.sidebar.header("📂 Data Inputs")
date_sheet_file = st.sidebar.file_uploader("1. Upload Date Sheet (CSV)", type=['csv'])
roster_file = st.sidebar.file_uploader("2. Upload Student Roster (CSV)", type=['csv'])
faculty_file = st.sidebar.file_uploader("3. Upload Faculty List (CSV)", type=['csv'])

# ==========================================
# PILLAR 1: DATA INGESTION & FILTERING
# ==========================================
st.header("📊 Pillar 1: Exam Schedule & Active Departments")

if date_sheet_file and roster_file:
    # Load the data
    date_sheet_df = pd.read_csv(date_sheet_file)
    roster_df = pd.read_csv(roster_file)
    
    # Extract unique dates for the dropdown
    unique_dates = date_sheet_df['Date'].dropna().unique()
    selected_date = st.selectbox("Select Exam Date:", unique_dates)
    
    # Filter active departments for the selected date
    active_exams = date_sheet_df[date_sheet_df['Date'] == selected_date]
    active_departments = active_exams['Department'].unique().tolist()
    
    st.success(f"Active Departments on {selected_date}: {', '.join(active_departments)}")
    st.dataframe(active_exams)

    # ==========================================
    # PILLAR 2: SEATING ALLOCATION (CHECKERBOARD)
    # ==========================================
    st.markdown("---")
    st.header("🪑 Pillar 2: Overflow-Aware Seating Engine")
    
    # Filter students who have exams today
    active_students = roster_df[roster_df['Department'].isin(active_departments)].copy()
    
    if not active_students.empty:
        
        # --- DYNAMIC CLASSROOM CONFIGURATION UI ---
        st.subheader("⚙️ Classroom Configuration")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            start_room = st.number_input("Starting Room No.", min_value=1, value=101)
        with col2:
            ROWS_PER_ROOM = st.number_input("Rows per Room", min_value=1, value=4)
        with col3:
            DESKS_PER_ROW = st.number_input("Desks per Row", min_value=1, value=6)
        with col4:
            STUDENTS_PER_DESK = st.number_input("Students per Desk", min_value=1, max_value=2, value=2)
            
        st.markdown("<br>", unsafe_allow_html=True) 
        
        if len(active_departments) == 1:
            st.warning(f"⚠️ Only one department ({active_departments[0]}) is active today. Checkerboard interleaving is disabled. Students will be seated sequentially.")
        
        if st.button("Generate Seating Arrangement"):
            with st.spinner("Calculating layout..."):
                
                active_students = active_students.sort_values(by=['Department', 'Roll_Number'])
                
                if len(active_departments) == 1:
                    queue_a = active_students.to_dict('records')
                    queue_b = [] 
                else:
                    half_point = len(active_departments) // 2
                    group_a_depts = active_departments[:half_point]
                    group_b_depts = active_departments[half_point:]
                    
                    queue_a = active_students[active_students['Department'].isin(group_a_depts)].to_dict('records')
                    queue_b = active_students[active_students['Department'].isin(group_b_depts)].to_dict('records')
                
                seating_plan = []
                room_number = start_room 
                
                while queue_a or queue_b:
                    room_allocation = []
                    for row in range(1, ROWS_PER_ROOM + 1):
                        for desk in range(1, DESKS_PER_ROW + 1):
                            student_a = queue_a.pop(0) if queue_a else None
                            
                            student_b = None
                            if STUDENTS_PER_DESK > 1:
                                student_b = queue_b.pop(0) if queue_b else None
                            
                            if student_a:
                                room_allocation.append({
                                    "Room": f"Room {room_number}",
                                    "Row": row,
                                    "Desk": desk,
                                    "Seat": "Left",
                                    "Roll_Number": student_a['Roll_Number'],
                                    "Department": student_a['Department']
                                })
                            if student_b:
                                room_allocation.append({
                                    "Room": f"Room {room_number}",
                                    "Row": row,
                                    "Desk": desk,
                                    "Seat": "Right",
                                    "Roll_Number": student_b['Roll_Number'],
                                    "Department": student_b['Department']
                                })
                                
                    seating_plan.extend(room_allocation)
                    room_number += 1
                
                seating_df = pd.DataFrame(seating_plan)
                st.session_state['seating_df'] = seating_df 
                
    if 'seating_df' in st.session_state:
        st.success("✅ Seating generated successfully!")
        st.dataframe(st.session_state['seating_df'])
    elif active_students.empty:
        st.warning("No students found in the roster for today's active exams.")

    # ==========================================
    # PILLAR 3: STRANGER PROTOCOL (CSV UPDATE)
    # ==========================================
    st.markdown("---")
    st.header("🕵️ Pillar 3: Stranger Protocol")

    if faculty_file is not None:
        faculty_df = pd.read_csv(faculty_file)
        faculty_df.columns = faculty_df.columns.str.strip() 
        
        st.subheader("🛡️ Stranger Protocol Active")
        st.info(f"Active Exams Today: {', '.join(active_departments)}")
        
        eligible_faculty = faculty_df[~faculty_df['Department'].isin(active_departments)].copy()
        
        if not eligible_faculty.empty:
            st.success(f"Found {len(eligible_faculty)} eligible invigilators with zero conflict of interest.")
            
            st.markdown("### 📋 Allocate Invigilators")
            invigilators_per_room = st.number_input("Target Invigilators per Room", min_value=1, value=2)
            st.write("Click the checkbox to select a teacher, type the Room Number, and **press Enter** before finalizing.")
            
            # --- THE FIX: Using st.session_state to prevent data wiping ---
            # Create a unique key for the current exam date
            state_key = f"faculty_editor_{selected_date}"
            
            # Only inject the blank columns if they don't already exist in memory
            if state_key not in st.session_state:
                eligible_faculty.insert(0, "Select", False)
                eligible_faculty["Assigned_Room"] = ""
                st.session_state[state_key] = eligible_faculty
            
            edited_faculty = st.data_editor(
                st.session_state[state_key],
                hide_index=True,
                column_config={
                    "Select": st.column_config.CheckboxColumn("Select", default=False),
                    "Assigned_Room": st.column_config.TextColumn("Assigned Room", help="Type the Room No and press Enter")
                },
                disabled=["Emp_ID", "Name", "Department", "Designation"] 
            )
            
            # Save edits back to memory so they survive the button click!
            st.session_state[state_key] = edited_faculty
            
            if st.button("Finalize Invigilation Plan"):
                final_allocation = edited_faculty[edited_faculty["Select"] == True]
                
                if final_allocation.empty:
                    st.warning("⚠️ You haven't selected any faculty members yet.")
                else:
                    st.success("✅ Invigilation Plan Finalized!")
                    st.dataframe(final_allocation[['Assigned_Room', 'Emp_ID', 'Name', 'Department', 'Designation']])
                    
        else:
            st.error("⚠️ CRITICAL ALERT: No neutral faculty available for invigilation today! Manual scheduling override required.")
    else:
        st.warning("⚠️ Please upload the Faculty CSV in the sidebar to proceed with invigilator assignment.")

else:
    st.info("👋 Welcome! Please upload your CSV files in the sidebar to begin.")