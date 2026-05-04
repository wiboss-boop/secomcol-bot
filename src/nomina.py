
"""
Genera el Excel de nómina mensual leyendo desde Google Sheets.
"""
import os
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from datetime import datetime

SPREADSHEET_ID = os.getenv("GOOGLE_SHEET_ID_ALARMAS")
TECNICOS = ["CRISTIAN", "MARTIN", "ALVARO", "YOHAN", "ERCS", "HANS", "DIEGO", "LUIS E", "JEAN", "JOEL", "DIANA"]
DESCUENTOS_FIJOS = ["ALTAS EN GARANTIA", "REUTILIZADAS GARANTIA", "AVERIAS EN GARANTIA", "IRPF", "EMBARGO"]

def get_sheet():
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())
    import gspread
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)

def leer_altas(wb, tecnico):
    try:
        ws = wb.worksheet(tecnico)
    except Exception:
        return []
    rows = ws.get_all_values()
    altas = []
    for row in rows[2:]:
        if len(row) >= 3 and row[1] and row[2]:
            orden, codigo = row[1].strip(), row[2].strip()
            if not orden or orden in ["-", "SECOMCOL"] or codigo in ["SIN ALTAS"] or "€" in codigo:
                continue
            fecha = row[0].strip() if row[0] else ""
            try:
                p = row[3].strip() if len(row) > 3 and row[3] else "0"
                p = p.replace("€","").replace(" ","").replace(",",".")
                precio = float(p) if p else 0
            except ValueError:
                precio = 0
            altas.append([fecha, orden, codigo, precio])
    # Deduplicar por orden
    vistas = set()
    altas_unicas = []
    for a in altas:
        if a[1] not in vistas:
            vistas.add(a[1])
            altas_unicas.append(a)
    return altas_unicas

def leer_descuentos(wb, tecnico):
    try:
        ws = wb.worksheet("Descuentos")
    except Exception:
        return {}
    rows = ws.get_all_values()
    desc = {}
    for row in rows[1:]:
        if len(row) >= 4 and row[1].strip().upper() == tecnico.upper():
            concepto = row[2].strip().upper()
            try:
                monto = float(row[3].strip().replace(",", "."))
            except ValueError:
                monto = 0
            desc[concepto] = desc.get(concepto, 0) + monto
    return desc

def escribir_hoja(ws, tecnico, altas, desc_vars, mes, ano):
    COL_F, COL_O, COL_C, COL_P = 5, 6, 7, 8
    font_b = Font(bold=True, name="Arial", size=10)
    font_n = Font(name="Arial", size=10)
    fill_h = PatternFill("solid", start_color="1F4E79", end_color="1F4E79")
    fill_d = PatternFill("solid", start_color="F2F2F2", end_color="F2F2F2")
    font_w = Font(bold=True, name="Arial", size=10, color="FFFFFF")

    for col, w in [(COL_F,12),(COL_O,22),(COL_C,12),(COL_P,10)]:
        ws.column_dimensions[get_column_letter(col)].width = w

    # Título
    c = ws.cell(row=25, column=COL_F, value=f"{tecnico} — {mes.upper()} {ano}")
    c.font = font_w; c.fill = fill_h; c.alignment = Alignment(horizontal="center")
    ws.merge_cells(start_row=25, start_column=COL_F, end_row=25, end_column=COL_P)

    # Cabeceras
    for col, lbl in [(COL_F,"FECHA"),(COL_O,"ORDEN"),(COL_C,"CODIGO"),(COL_P,"TECNICO")]:
        c = ws.cell(row=26, column=col, value=lbl)
        c.font = font_w; c.fill = fill_h; c.alignment = Alignment(horizontal="center")

    # Altas
    row = 27
    first_row = row
    for fecha, orden, codigo, precio in altas:
        ws.cell(row=row, column=COL_F, value=fecha).font = font_n
        ws.cell(row=row, column=COL_O, value=orden).font = font_n
        ws.cell(row=row, column=COL_C, value=codigo).font = font_n
        ws.cell(row=row, column=COL_P, value=precio).font = font_n
        row += 1
    last_row = row - 1

    # FESTIVOS
    row += 1
    ws.cell(row=row, column=COL_O, value="FESTIVOS").font = font_b
    ws.cell(row=row, column=COL_P, value=0).font = font_n
    festivos_row = row; row += 1

    # Subtotal altas
    sub_row = row
    ws.cell(row=row, column=COL_P, value=f"=SUM(H{first_row}:H{last_row})+H{festivos_row}").font = font_b
    row += 2

    # DESCUENTOS título
    ws.cell(row=row, column=COL_O, value="DESCUENTOS").font = Font(bold=True, name="Arial", size=10, color="C00000")
    row += 1
    desc_start = row

    # Fijos
    fijos_rows = {}
    for concepto in DESCUENTOS_FIJOS:
        c_l = ws.cell(row=row, column=COL_O, value=concepto)
        c_l.font = font_n; c_l.fill = fill_d
        valor = desc_vars.pop(concepto, 0)
        c_v = ws.cell(row=row, column=COL_P, value=valor)
        c_v.font = font_n; c_v.fill = fill_d
        fijos_rows[concepto] = row
        row += 1

    # Variables adicionales
    for concepto, monto in sorted(desc_vars.items()):
        c_l = ws.cell(row=row, column=COL_O, value=concepto)
        c_l.font = font_n; c_l.fill = fill_d
        c_v = ws.cell(row=row, column=COL_P, value=monto)
        c_v.font = font_n; c_v.fill = fill_d
        row += 1

    desc_end = row - 1

    # SUBTOTAL descuentos
    ws.cell(row=row, column=COL_O, value="SUBTOTAL").font = font_b
    ws.cell(row=row, column=COL_P, value=f"=SUM(H{desc_start}:H{desc_end})").font = font_b
    sub_desc_row = row; row += 2

    # TOTAL
    c_l = ws.cell(row=row, column=COL_O, value="TOTAL")
    c_l.font = Font(bold=True, name="Arial", size=12, color="FFFFFF")
    c_l.fill = PatternFill("solid", start_color="1F4E79", end_color="1F4E79")
    ws.cell(row=row, column=COL_P, value=f"=H{sub_row}-H{sub_desc_row}").font = Font(bold=True, name="Arial", size=12)

def generar_excel(mes, ano):
    wb_sheet = get_sheet()
    wb = Workbook()
    wb.remove(wb.active)
    for tecnico in TECNICOS:
        altas = leer_altas(wb_sheet, tecnico)
        desc = leer_descuentos(wb_sheet, tecnico)
        ws = wb.create_sheet(title=tecnico)
        escribir_hoja(ws, tecnico, altas, desc, mes, ano)
    nombre = f"ALTAS_{mes.upper()}_{ano}.xlsx"
    wb.save(nombre)
    return nombre
