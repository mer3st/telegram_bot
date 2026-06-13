import zipfile
import json
import re
import requests


class PowerBIMentor:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.api_url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent"
        )

    # ------------------------------------------------------------------
    # PBIT PARSING
    # ------------------------------------------------------------------

    def extract_schema(self, pbit_path: str) -> dict:
        """
        Returns a structured summary instead of raw JSON blobs.
        Keys: m_code, tables, measures, calculated_columns,
              relationships, visuals, error (optional)
        """
        result = {
            "m_code": "",
            "tables": [],
            "measures": [],
            "calculated_columns": [],
            "relationships": [],
            "visuals": [],
        }
        try:
            with zipfile.ZipFile(pbit_path, "r") as z:
                names = z.namelist()

                # ── Power Query M code ──────────────────────────────────
                if "Mashup/Package/Formulas/Section1.m" in names:
                    result["m_code"] = z.read(
                        "Mashup/Package/Formulas/Section1.m"
                    ).decode("utf-8", errors="ignore")

                # ── Data model schema ───────────────────────────────────
                if "DataModelSchema" in names:
                    raw = z.read("DataModelSchema").decode(
                        "utf-16-le", errors="ignore"
                    )
                    self._parse_model_schema(raw, result)

                # ── Report layout ───────────────────────────────────────
                if "Report/Layout" in names:
                    raw_layout = z.read("Report/Layout").decode(
                        "utf-16-le", errors="ignore"
                    )
                    self._parse_layout(raw_layout, result)

        except Exception as e:
            result["error"] = str(e)

        return result

    def _parse_model_schema(self, raw: str, result: dict):
        """Parse tables, measures, calculated columns, and relationships."""
        try:
            data = json.loads(raw)
        except Exception:
            # Fall back to regex if JSON parse fails
            self._parse_model_schema_regex(raw, result)
            return

        model = data.get("model", {})

        # Tables
        for table in model.get("tables", []):
            tname = table.get("name", "")
            if tname.startswith("LocalDate") or tname.startswith("DateTable"):
                continue  # skip auto-generated date tables
            result["tables"].append(tname)

            # Measures
            for m in table.get("measures", []):
                expr = m.get("expression", "")
                if isinstance(expr, list):
                    expr = "\n".join(line for line in expr if line.strip())
                result["measures"].append({
                    "table": tname,
                    "name": m.get("name", ""),
                    "expression": expr.strip(),
                })

            # Calculated columns
            for col in table.get("columns", []):
                if col.get("type") == "calculated":
                    expr = col.get("expression", "")
                    if isinstance(expr, list):
                        expr = "\n".join(line for line in expr if line.strip())
                    result["calculated_columns"].append({
                        "table": tname,
                        "name": col.get("name", ""),
                        "expression": expr.strip(),
                    })

        # Relationships (skip auto LocalDateTable ones)
        for rel in model.get("relationships", []):
            ft = rel.get("fromTable", "")
            tt = rel.get("toTable", "")
            if "LocalDate" in ft or "LocalDate" in tt:
                continue
            result["relationships"].append({
                "from": f"{ft}[{rel.get('fromColumn','')}]",
                "to": f"{tt}[{rel.get('toColumn','')}]",
                "active": not rel.get("isActive") == False,
            })

    def _parse_model_schema_regex(self, raw: str, result: dict):
        """Regex fallback for schema parsing."""
        # #9: broad name scan — exclude known auto-generated prefixes
        _skip = ("LocalDate", "DateTable", "$", "__")
        result["tables"] = list(dict.fromkeys([
            t for t in re.findall(r'"name":\s*"([^"]+)"', raw)
            if not any(t.startswith(p) for p in _skip) and len(t) < 100
        ]))[:50]

        # Named measures/calculated columns with expressions
        for name, expr in re.findall(
            r'"name":\s*"([^"]+)",\s*"expression":\s*"([^"]+)"', raw
        ):
            result["measures"].append({"name": name, "expression": expr})

    def _parse_layout(self, raw: str, result: dict):
        """Parse report pages and visual types/configurations."""
        try:
            data = json.loads(raw)
        except Exception:
            return

        for section in data.get("sections", []):
            page_name = section.get("displayName", "")
            visuals_on_page = []

            for vc in section.get("visualContainers", []):
                config_str = vc.get("config", "{}")
                try:
                    config = json.loads(config_str)
                except Exception:
                    continue

                sv = config.get("singleVisual", {})
                vtype = sv.get("visualType", "unknown")
                projections = sv.get("projections", {})

                rows = [p.get("queryRef", "") for p in projections.get("Rows", [])]
                cols = [p.get("queryRef", "") for p in projections.get("Columns", [])]
                vals = [p.get("queryRef", "") for p in projections.get("Values", [])]

                # Check for switchValuesToRows in objects/properties
                objects = sv.get("objects", {})
                values_cfg = objects.get("values", [{}])
                switch_to_rows = False
                for obj in values_cfg:
                    props = obj.get("properties", {})
                    if props.get("switchValuesToRows", {}).get("expr", {}).get(
                        "Literal", {}
                    ).get("Value") == "true":
                        switch_to_rows = True

                # Check tabular layout
                general_cfg = objects.get("general", [{}])
                tabular = False
                for obj in general_cfg:
                    props = obj.get("properties", {})
                    if "rowSubtotals" in props or "outlineStyle" in str(props):
                        tabular = True

                visuals_on_page.append({
                    "type": vtype,
                    "rows": rows,
                    "columns": cols,
                    "values": vals,
                    "switchValuesToRows": switch_to_rows,
                    "tabularLayout": tabular,
                })

            result["visuals"].append({
                "page": page_name,
                "visuals": visuals_on_page,
            })

    # ------------------------------------------------------------------
    # STRUCTURED SUMMARY → PROMPT
    # ------------------------------------------------------------------

    def build_structured_summary(self, schema: dict) -> str:
        """Convert parsed schema into a readable summary for the AI."""
        lines = []

        lines.append("=== TABLES ===")
        lines.append(", ".join(schema["tables"]) or "None found")

        lines.append("\n=== MEASURES ===")
        if schema["measures"]:
            for m in schema["measures"]:
                tbl = m.get("table", "")
                prefix = f"[{tbl}] " if tbl else ""
                lines.append(f"  {prefix}{m['name']}: {m['expression']}")
        else:
            lines.append("  None found")

        lines.append("\n=== CALCULATED COLUMNS ===")
        if schema["calculated_columns"]:
            for c in schema["calculated_columns"]:
                lines.append(
                    f"  [{c['table']}] {c['name']}: {c['expression'][:200]}"
                )
        else:
            lines.append("  None found")

        lines.append("\n=== RELATIONSHIPS (non-auto) ===")
        if schema["relationships"]:
            for r in schema["relationships"]:
                active_label = "ACTIVE" if r["active"] else "inactive"
                lines.append(f"  {r['from']} → {r['to']}  ({active_label})")
        else:
            lines.append("  None found")

        lines.append("\n=== REPORT VISUALS (by page) ===")
        for page in schema["visuals"]:
            lines.append(f"  Page: '{page['page']}'")
            for v in page["visuals"]:
                lines.append(f"    Visual type: {v['type']}")
                if v["rows"]:
                    lines.append(f"      Rows: {' → '.join(v['rows'])}")
                if v["columns"]:
                    lines.append(f"      Columns: {', '.join(v['columns'])}")
                if v["values"]:
                    lines.append(f"      Values: {', '.join(v['values'])}")
                lines.append(
                    f"      switchValuesToRows: {v['switchValuesToRows']}"
                )
                lines.append(f"      tabularLayout: {v['tabularLayout']}")

        lines.append("\n=== M-CODE (Power Query) ===")
        lines.append(schema["m_code"][:10000] or "Not found")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # AI CALL
    # ------------------------------------------------------------------

    def ask_gemini(self, prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(
            f"{self.api_url}?key={self.api_key}",
            headers=headers,
            json=body,
            timeout=120,
        )
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Gemini API xatosi: {data['error']}")
        candidates = data.get("candidates")
        if candidates and candidates[0].get("content", {}).get("parts"):
            return candidates[0]["content"]["parts"][0].get("text", "")
        raise RuntimeError(f"Gemini bo'sh javob qaytardi: {data}")

    # ------------------------------------------------------------------
    # MAIN EVALUATE
    # ------------------------------------------------------------------

    def evaluate_all(
        self, answer_path: str, questions: dict = None, description: str = ""
    ) -> dict:
        schema = self.extract_schema(answer_path)

        # #2: raise so the caller's retry logic activates instead of saving a 0
        if "error" in schema:
            raise RuntimeError(f"Faylni ochishda xato: {schema['error']}")

        summary = self.build_structured_summary(schema)

        # #1: use homework description when structured criteria aren't provided
        task_context = description.strip()
        if not task_context:
            q = questions or {}
            parts = [
                f"DAX: {q['dax']}" if q.get("dax") else "",
                f"Vizual: {q['visual']}" if q.get("visual") else "",
                f"Yozish: {q['write']}" if q.get("write") else "",
            ]
            task_context = "\n".join(p for p in parts if p)
        if not task_context:
            task_context = "Umumiy Power BI loyihasini baholang: ma'lumot modeli, DAX o'lchovlar, vizuallar va Power Query."

        full_prompt = f"""
Siz Power BI vazifalarini baholovchi ekspertsiz.
Quyida talabaning .pbit fayli to'g'ridan-to'g'ri tahlil qilingan ANIQ ma'lumotlar berilgan.

{summary}

=== VAZIFA TALABLARI ===
{task_context}

=== BAHOLASH QOIDALARI ===
1. FAQAT yuqoridagi struktural tahlil ma'lumotlariga asoslan. O'zingdan hech narsa qo'shma.
2. Har bir mezon uchun: topildi/topilmadi/qisman — aniq ko'rsat.
3. Har bir mezon uchun necha ball berildi yoki ayirildi — raqam bilan yoz.
4. Umumiy ballni hisobla va BALL: formatida yoz.
5. MUHIM: Feedbackni talabaga to'g'ridan-to'g'ri yoz (masalan: "Siz bu yerda...", "Siz to'g'ri qildingiz...", "Siz X ni o'tkazib yubordingiz..."). Ustozga emas, talabaning o'ziga murojaat qil.

Quyidagi ANIQ formatda javob ber — boshqa hech narsa yozma:

BALL: [0-100]

FIKR:
--- DAX O'LCHOVLAR ---
[mezon nomi]: [topildi/topilmadi] — [ball +X yoki -X] — [1 jumla izoh]
[mezon nomi]: [topildi/topilmadi] — [ball +X yoki -X] — [1 jumla izoh]
...
DAX jami: X/[max ball]

--- VIZUAL ---
[mezon nomi]: [topildi/topilmadi/qisman] — [ball +X yoki -X] — [1 jumla izoh]
...
Vizual jami: X/[max ball]

--- MA'LUMOT MODELI ---
[mezon nomi]: [topildi/topilmadi] — [ball +X yoki -X] — [1 jumla izoh]
...
Model jami: X/[max ball]

XULOSA: [2 jumla — nima yaxshi, nima yetishmayotgani]
"""

        raw_response = self.ask_gemini(full_prompt)

        score = 0
        feedback = raw_response

        # #4: case-insensitive, tolerates **BALL:** markdown bold from Gemini
        score_match = re.search(r"(?i)\*{0,2}BALL:\*{0,2}\s*\*{0,2}(\d+)", raw_response)
        feedback_match = re.search(r"(?i)FIKR:\s*([\s\S]+)", raw_response)

        if score_match:
            score = max(0, min(int(score_match.group(1)), 100))  # #11: clamp [0,100]
        if feedback_match:
            feedback = feedback_match.group(1).strip()

        return {"score": score, "feedback": feedback}
    

    def evaluate_text(self, student_text: str, expected: str, criteria: str = "") -> dict:
        """Compare a student's extracted text answer against an expected answer."""
        if not student_text.strip():
            return {"score": 0, "feedback": "Fayldan matn o'qib bo'lmadi."}

        prompt = f"""
Siz vazifalarni baholovchi ekspertsiz. Talabaning javobini KUTILGAN javob bilan solishtiring.

=== KUTILGAN JAVOB (to'g'ri yechim) ===
{expected}

=== TALABA JAVOBI ===
{student_text[:20000]}

=== QO'SHIMCHA MEZONLAR ===
{criteria or "Yo'q"}

=== BAHOLASH QOIDALARI ===
1. Talaba javobini kutilgan javob bilan solishtir.
2. Har bir muhim nuqta uchun: mos / qisman / mos emas — ko'rsat.
3. Umumiy ballni 0-100 oralig'ida hisobla.
4. MUHIM: Feedbackni talabaga to'g'ridan-to'g'ri yoz (masalan: "Siz bu yerda...", "Siz to'g'ri yozdingiz...", "Siz X ni o'tkazib yubordingiz..."). Ustozga emas, talabaning o'ziga murojaat qil.

Quyidagi ANIQ formatda javob ber — boshqa hech narsa yozma:

BALL: [0-100]

FIKR:
[har bir nuqta uchun bitta qator izoh]

XULOSA: [2 jumla — nima to'g'ri, nima yetishmayapti]
"""
        raw = self.ask_gemini(prompt)

        score = 0
        feedback = raw
        m = re.search(r"(?i)\*{0,2}BALL:\*{0,2}\s*\*{0,2}(\d+)", raw)  # #4
        fm = re.search(r"(?i)FIKR:\s*([\s\S]+)", raw)
        if m:
            score = max(0, min(int(m.group(1)), 100))  # #11: clamp [0,100]
        if fm:
            feedback = fm.group(1).strip()
        return {"score": score, "feedback": feedback}