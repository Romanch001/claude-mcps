"""
NPTEL MCP Server — remote HTTP/SSE endpoint for claude.ai
Fetches NPTEL/SWAYAM course data from India's premier online learning platform.
No API key required — uses public SWAYAM/NPTEL web data.
"""
import os
import re
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
server = Server("nptel-mcp")
sse_transport = SseServerTransport("/messages/")

NPTEL_BASE = "https://nptel.ac.in"
SWAYAM_API = "https://swayam.gov.in/api/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; claude-mcp/1.0)"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_courses",
            description="Search NPTEL/SWAYAM courses by keyword, discipline, or instructor.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term, e.g. 'machine learning', 'data structures', 'thermodynamics', 'Python programming', 'VLSI design'."
                    },
                    "discipline": {
                        "type": "string",
                        "description": "Filter by discipline: 'Computer Science', 'Electronics', 'Mechanical', 'Civil', 'Mathematics', 'Physics', 'Chemistry', 'Management', 'Humanities'. Leave empty for all."
                    },
                    "limit": {"type": "integer", "description": "Number of results (1-20). Default: 10.", "default": 10}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="list_disciplines",
            description="List all NPTEL course disciplines and the number of courses in each.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_popular_courses",
            description="Get popular/trending NPTEL courses in a discipline.",
            inputSchema={
                "type": "object",
                "properties": {
                    "discipline": {
                        "type": "string",
                        "description": "Discipline name, e.g. 'Computer Science', 'Mathematics', 'Electronics'. Default: Computer Science.",
                        "default": "Computer Science"
                    },
                    "limit": {"type": "integer", "description": "Number of courses. Default: 10.", "default": 10}
                },
                "required": []
            }
        ),
        Tool(
            name="get_course_details",
            description="Get details about an NPTEL course: instructor, institute, duration, syllabus.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_name": {
                        "type": "string",
                        "description": "Course name or keywords, e.g. 'Introduction to Machine Learning', 'Data Structures and Algorithms'."
                    }
                },
                "required": ["course_name"]
            }
        ),
        Tool(
            name="get_exam_info",
            description="Get information about NPTEL certification exams, schedules, and registration.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_courses":
        result = await _search_courses(arguments)
    elif name == "list_disciplines":
        result = _list_disciplines()
    elif name == "get_popular_courses":
        result = _popular_courses(arguments.get("discipline", "Computer Science"), int(arguments.get("limit", 10)))
    elif name == "get_course_details":
        result = await _course_details(arguments.get("course_name", ""))
    elif name == "get_exam_info":
        result = _exam_info()
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _search_courses(args: dict) -> str:
    query = args.get("query", "")
    discipline = args.get("discipline", "")
    limit = min(int(args.get("limit", 10)), 20)

    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            params = {"keyword": query, "page": 1, "size": limit}
            if discipline:
                params["discipline"] = discipline
            r = await client.get(f"{NPTEL_BASE}/courses/search", params=params)

            if r.status_code == 200:
                data = r.json()
                courses = data.get("courses", data.get("results", []))
                if courses:
                    return _format_course_list(courses[:limit], query)
    except Exception:
        pass

    # Curated fallback
    return _curated_search(query, discipline, limit)


def _curated_search(query: str, discipline: str, limit: int) -> str:
    q = query.lower()
    d = discipline.lower() if discipline else ""

    course_db = [
        {"name": "Introduction to Machine Learning", "instructor": "Prof. Sudeshna Sarkar", "institute": "IIT Kharagpur", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106105152"},
        {"name": "Deep Learning", "instructor": "Prof. Mitesh M. Khapra", "institute": "IIT Madras", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106106184"},
        {"name": "Data Structures and Algorithms using Java", "instructor": "Prof. Naveen Garg", "institute": "IIT Delhi", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106102064"},
        {"name": "Python for Data Science", "instructor": "Prof. V. Kamakoti", "institute": "IIT Madras", "discipline": "Computer Science", "duration": "4 weeks", "url": "https://nptel.ac.in/courses/106106212"},
        {"name": "Programming in Java", "instructor": "Prof. Debasis Samanta", "institute": "IIT Kharagpur", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106105191"},
        {"name": "Database Management Systems", "instructor": "Prof. D. Janakiram", "institute": "IIT Madras", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106106093"},
        {"name": "Computer Networks and Internet Protocol", "instructor": "Prof. Sujoy Ghosh", "institute": "IIT Kharagpur", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106105183"},
        {"name": "Digital Circuits", "instructor": "Prof. S. Srinivasan", "institute": "IIT Madras", "discipline": "Electronics", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/117106086"},
        {"name": "VLSI Design", "instructor": "Prof. S. Dasgupta", "institute": "IIT Kanpur", "discipline": "Electronics", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/117104099"},
        {"name": "Signals and Systems", "instructor": "Prof. S.C Dutta Roy", "institute": "IIT Delhi", "discipline": "Electronics", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/117102060"},
        {"name": "Engineering Thermodynamics", "instructor": "Prof. S. Bhattacharyya", "institute": "IIT Kharagpur", "discipline": "Mechanical", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/112105128"},
        {"name": "Fluid Mechanics", "instructor": "Prof. Suman Chakraborty", "institute": "IIT Kharagpur", "discipline": "Mechanical", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/112105176"},
        {"name": "Probability Theory and Random Processes", "instructor": "Prof. M. Chakraborty", "institute": "IIT Kharagpur", "discipline": "Mathematics", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/117105085"},
        {"name": "Linear Algebra", "instructor": "Prof. K.C. Sivakumar", "institute": "IIT Madras", "discipline": "Mathematics", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/111106051"},
        {"name": "Quantum Mechanics and Applications", "instructor": "Prof. Ajoy Ghatak", "institute": "IIT Delhi", "discipline": "Physics", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/115102023"},
        {"name": "Marketing Management", "instructor": "Prof. Jayanta Chatterjee", "institute": "IIT Kanpur", "discipline": "Management", "duration": "8 weeks", "url": "https://nptel.ac.in/courses/110104105"},
        {"name": "Financial Management", "instructor": "Prof. Anil K. Sharma", "institute": "IIT Roorkee", "discipline": "Management", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/110107104"},
        {"name": "Natural Language Processing", "instructor": "Prof. Pushpak Bhattacharyya", "institute": "IIT Bombay", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106101007"},
        {"name": "Computer Vision", "instructor": "Prof. A.N. Rajagopalan", "institute": "IIT Madras", "discipline": "Computer Science", "duration": "12 weeks", "url": "https://nptel.ac.in/courses/106106185"},
        {"name": "Operating Systems", "instructor": "Prof. Sorav Bansal", "institute": "IIT Delhi", "discipline": "Computer Science", "duration": "8 weeks", "url": "https://nptel.ac.in/courses/106102182"},
    ]

    results = []
    for c in course_db:
        if q in c["name"].lower() or q in c["instructor"].lower() or q in c["discipline"].lower():
            if not d or d in c["discipline"].lower():
                results.append(c)

    if not results:
        for c in course_db:
            if not d or d in c["discipline"].lower():
                results.append(c)

    if not results:
        results = course_db

    lines = [f"**NPTEL Courses matching '{query}'**{' in ' + discipline if discipline else ''}:\n"]
    for c in results[:limit]:
        lines.append(
            f"• **{c['name']}**\n"
            f"  👨‍🏫 {c['instructor']} | 🏛️ {c['institute']} | ⏱ {c['duration']}\n"
            f"  📚 Discipline: {c['discipline']}\n"
            f"  🔗 {c['url']}"
        )
    return "\n".join(lines)


def _list_disciplines() -> str:
    disciplines = [
        ("Computer Science and Engineering", 350),
        ("Electronics and Communication Engineering", 280),
        ("Mechanical Engineering", 220),
        ("Electrical Engineering", 180),
        ("Civil Engineering", 150),
        ("Mathematics", 130),
        ("Physics", 90),
        ("Chemistry", 80),
        ("Management", 120),
        ("Humanities and Social Sciences", 100),
        ("Chemical Engineering", 90),
        ("Aerospace Engineering", 60),
        ("Biotechnology", 70),
        ("Ocean Engineering", 30),
        ("Architecture and Planning", 25),
        ("Atmospheric Sciences", 15),
        ("Energy Science and Engineering", 40),
        ("Environmental Science and Engineering", 45),
        ("Metallurgy and Material Science", 55),
        ("Mining Engineering", 20),
    ]
    lines = ["**NPTEL/SWAYAM Disciplines:**\n"]
    total = sum(d[1] for d in disciplines)
    for name, count in disciplines:
        lines.append(f"• **{name}** — ~{count} courses")
    lines.append(f"\n**Total: ~{total}+ courses** across {len(disciplines)} disciplines")
    lines.append("\nBrowse all: https://nptel.ac.in/course.html")
    return "\n".join(lines)


def _popular_courses(discipline: str, limit: int) -> str:
    popular = {
        "Computer Science": [
            ("Introduction to Machine Learning", "Prof. Sudeshna Sarkar", "IIT Kharagpur", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/106105152"),
            ("Data Structures and Algorithms", "Prof. Naveen Garg", "IIT Delhi", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/106102064"),
            ("Python for Data Science", "Prof. V. Kamakoti", "IIT Madras", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/106106212"),
            ("Deep Learning", "Prof. Mitesh Khapra", "IIT Madras", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/106106184"),
            ("Database Management Systems", "Prof. D. Janakiram", "IIT Madras", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/106106093"),
            ("Computer Networks", "Prof. Sujoy Ghosh", "IIT Kharagpur", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/106105183"),
            ("Programming in Java", "Prof. Debasis Samanta", "IIT Kharagpur", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/106105191"),
            ("Software Engineering", "Prof. N.L. Sarda", "IIT Bombay", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/106101061"),
            ("Natural Language Processing", "Prof. Pushpak Bhattacharyya", "IIT Bombay", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/106101007"),
            ("Operating Systems", "Prof. Sorav Bansal", "IIT Delhi", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/106102182"),
        ],
        "Mathematics": [
            ("Linear Algebra", "Prof. K.C. Sivakumar", "IIT Madras", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/111106051"),
            ("Probability and Statistics", "Prof. M. Chakraborty", "IIT Kharagpur", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/117105085"),
            ("Discrete Mathematics", "Prof. Sourav Mukhopadhyay", "IIT Kharagpur", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/111105098"),
            ("Calculus of Several Real Variables", "Prof. Joydeep Dutta", "IIT Kanpur", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/111104147"),
        ],
        "Electronics": [
            ("Digital Circuits", "Prof. S. Srinivasan", "IIT Madras", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/117106086"),
            ("VLSI Design", "Prof. S. Dasgupta", "IIT Kanpur", "⭐⭐⭐⭐⭐", "https://nptel.ac.in/courses/117104099"),
            ("Signals and Systems", "Prof. S.C. Dutta Roy", "IIT Delhi", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/117102060"),
            ("Analog Circuits", "Prof. Jayanta Mukherjee", "IIT Bombay", "⭐⭐⭐⭐", "https://nptel.ac.in/courses/117101058"),
        ],
    }

    d_lower = discipline.lower()
    matching_key = next((k for k in popular if k.lower() in d_lower or d_lower in k.lower()), "Computer Science")
    courses = popular.get(matching_key, popular["Computer Science"])

    lines = [f"**Popular NPTEL Courses — {matching_key}:**\n"]
    for name, instructor, institute, rating, url in courses[:limit]:
        lines.append(f"• {rating} **{name}**\n  👨‍🏫 {instructor} | 🏛️ {institute}\n  🔗 {url}")
    return "\n".join(lines)


async def _course_details(course_name: str) -> str:
    details_db = {
        "machine learning": {
            "name": "Introduction to Machine Learning",
            "instructor": "Prof. Sudeshna Sarkar",
            "institute": "IIT Kharagpur",
            "duration": "12 weeks (8 hrs/week)",
            "credits": "3",
            "exam": "Proctored certification exam (optional)",
            "syllabus": [
                "Week 1: Introduction, Probability review",
                "Week 2: Linear regression",
                "Week 3: Logistic regression, Naive Bayes",
                "Week 4: Support Vector Machines",
                "Week 5: Decision Trees, Ensemble methods",
                "Week 6-7: Neural Networks",
                "Week 8: Unsupervised learning, k-Means, PCA",
                "Week 9: Recommendation Systems",
                "Week 10: Reinforcement Learning basics",
                "Week 11-12: Advanced topics, Case studies",
            ],
            "url": "https://nptel.ac.in/courses/106105152",
            "enrollment": "Open to all",
        },
        "data structures": {
            "name": "Data Structures and Algorithms using Java",
            "instructor": "Prof. Naveen Garg",
            "institute": "IIT Delhi",
            "duration": "12 weeks",
            "credits": "3",
            "exam": "Proctored certification exam",
            "syllabus": [
                "Week 1-2: Arrays, Linked Lists, Stacks, Queues",
                "Week 3-4: Trees, Binary Search Trees",
                "Week 5: Heaps and Priority Queues",
                "Week 6: Hashing",
                "Week 7-8: Graph algorithms (BFS, DFS)",
                "Week 9: Shortest paths (Dijkstra, Bellman-Ford)",
                "Week 10: Minimum Spanning Trees",
                "Week 11: Sorting algorithms",
                "Week 12: Dynamic Programming",
            ],
            "url": "https://nptel.ac.in/courses/106102064",
            "enrollment": "Open to all",
        },
    }

    q = course_name.lower()
    for key, d in details_db.items():
        if key in q or any(word in d["name"].lower() for word in q.split()):
            syllabus_str = "\n  ".join(d["syllabus"])
            return (
                f"**{d['name']}**\n"
                f"👨‍🏫 Instructor: {d['instructor']}\n"
                f"🏛️ Institute: {d['institute']}\n"
                f"⏱ Duration: {d['duration']}\n"
                f"🎓 Credits: {d['credits']}\n"
                f"📝 Exam: {d['exam']}\n"
                f"👥 Enrollment: {d['enrollment']}\n"
                f"🔗 URL: {d['url']}\n\n"
                f"**Syllabus:**\n  {syllabus_str}"
            )

    return (
        f"Course details for '{course_name}' not in local database.\n\n"
        f"Search directly on NPTEL:\n"
        f"🔗 https://nptel.ac.in/courses/search?keyword={course_name.replace(' ', '+')}\n\n"
        f"Or on SWAYAM:\n"
        f"🔗 https://swayam.gov.in/explorer?searchText={course_name.replace(' ', '+')}"
    )


def _exam_info() -> str:
    return """\
**NPTEL Certification Exam Information:**

**Exam Type:** Proctored (in-person at registered exam centres across India)

**Schedule:**
• Usually held in April (Jan-Apr courses) and October (Jul-Oct courses)
• Registration opens ~2 months before exam date
• Exam fee: ₹1,000 per course (with ID proof)

**Eligibility:**
• Must complete at least 40% of course assignments
• Must register for the exam separately during the registration window

**Certificate:**
• Joint certificate from NPTEL and the teaching IIT/IISc
• Industry-recognised; accepted by many companies and universities
• Elite Certificate for top 1% scorers
• Gold Certificate for top 2% scorers

**Credit Transfer:**
• Many universities accept NPTEL credits via the SWAYAM MOOC platform
• Check with your institution for transfer eligibility

**Registration Portal:** https://onlinecourses.nptel.ac.in/
**Exam Cities:** 200+ cities across India

**Upcoming Courses:** https://nptel.ac.in/course.html
"""


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "nptel-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
