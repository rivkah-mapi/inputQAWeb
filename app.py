import os
import glob
import csv
import tkinter as tk
from tkinter import filedialog
import rasterio
import geopandas as gpd
from flask import Flask, render_template, jsonify, request


app = Flask(__name__)


progress_info = {"percent": 0, "message": "ממתין..."}


def extract_raster_info(sheet_id, src, geometry):
    return {
        "Sheet": sheet_id,
        "EPSG": src.crs.to_epsg() if src.crs else 0,
        "Res": round(src.res[0], 3),
        "Format": src.driver,
        "Width_M": round(src.bounds.right - src.bounds.left, 2),
        "Height_M": round(src.bounds.top - src.bounds.bottom, 2),
        "Width_PX": src.width,
        "Height_PX": src.height,
        "Bands": src.count,
        "DataType": str(src.dtypes[0]),
        "geometry": geometry
    }


# פונקציות בחירת קבצים
def open_directory_dialog():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    path = filedialog.askdirectory()
    root.destroy()
    return path


def open_file_dialog():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    path = filedialog.askopenfilename(filetypes=[("Shapefiles", "*.shp")])
    root.destroy()
    return path


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/browse_folder', methods=['POST'])
def browse_folder():
    return jsonify({'path': open_directory_dialog()})


@app.route('/browse_file', methods=['POST'])
def browse_file():
    return jsonify({'path': open_file_dialog()})


@app.route('/get_progress')
def get_progress():
    return jsonify(progress_info)


@app.route('/run_qa', methods=['POST'])
def run_qa():
    try:
        data = request.json
       
        # 1. קבלת נתונים מהממשק
        o_idx_raw = data.get('ortho_index')
        d_idx_raw = data.get('dsm_index')
        o_res_val = data.get('ortho_res')
        d_res_val = data.get('dsm_res')
        o_folder_raw = data.get('ortho_folder')
        d_folder_raw = data.get('dsm_folder')


        # בדיקות קלט
        # בדיקה 1: אם הוכנס אינדקס אורתופוטו (1), חובה להזין רזולוציה (3) ותיקייה (5)
        if o_idx_raw:
            if not o_res_val or not o_folder_raw:
                return jsonify({
                    'status': 'error',
                    'message': 'עבור בדיקת אורתופוטו חובה להזין גם רזולוציה וגם תיקיית תמונות.'
                })


        # בדיקה 2: אם הוכנס אינדקס DSM (2), חובה להזין רזולוציה (4) ותיקייה (6)
        if d_idx_raw:
            if not d_res_val or not d_folder_raw:
                return jsonify({
                    'status': 'error',
                    'message': 'עבור בדיקת DSM חובה להזין גם רזולוציה וגם תיקיית תמונות.'
                })


        # בדיקה בסיסית: האם הוזן קלט כלשהו?
        if not o_idx_raw and not d_idx_raw:
            return jsonify({
                'status': 'error',
                'message': 'לא הוזן קלט לביצוע. יש להזין נתוני אורתו או נתוני DSM.'
            })


        o_idx_path = os.path.abspath(os.path.normpath(o_idx_raw)) if o_idx_raw else ""
        d_idx_path = os.path.abspath(os.path.normpath(d_idx_raw)) if d_idx_raw else ""
        o_folder = os.path.abspath(os.path.normpath(o_folder_raw)) if o_folder_raw else ""
        d_folder = os.path.abspath(os.path.normpath(d_folder_raw)) if d_folder_raw else ""


        ortho_ready = bool(o_idx_path and o_res_val and o_folder)
        dsm_ready = bool(d_idx_path and d_res_val and d_folder)


        if not ortho_ready and not dsm_ready:
            return jsonify({'status': 'error', 'message': 'חסר קלט: יש להזין את כל השדות הנדרשים לאורתו או ל-DSM.'})


        # הגדרת נתיבי פלט
        output_base = os.path.join(os.path.expanduser("~"), "Documents", "QA_Results")
        if not os.path.exists(output_base):
            os.makedirs(output_base)
        summary_csv = os.path.join(output_base, "QA_Final_Summary.csv")
       
        # טעינת האינדקס
        work_idx = o_idx_path if ortho_ready else d_idx_path
        gdf_index = gpd.read_file(work_idx)
       
        # אתחול מונים בדיוק לפי הקוד המקורי
        counters = {k: 0 for k in ["1001", "1002_fmt", "1002_type", "1002_bands", "1003",
                                   "1004_fmt", "1004_type", "1004_bands", "1005", "1006"]}
       
        error_records = []
        ortho_info_records = []
        dsm_info_records = []


        progress_info.update({"percent": 0, "message": "מתחיל עיבוד נתונים..."})
        gdf_index = gpd.read_file(work_idx)
        total = len(gdf_index)
   
        # לולאת הרצה על הגליונות
        for i, row in gdf_index.iterrows():
            sheet_id = str(row['Sheet'])
            sheet_geom = row['geometry']


            progress_info["percent"] = int(((i + 1) / total) * 100)
            progress_info["message"] = f"בודק גליון {i+1} מתוך {total}: {sheet_id}"


            all_current_errors = []
           
            o_raster_bound = None
            d_raster_bound = None
            o_tolerance = 0.01
            d_tolerance = 0.11


            # --- בדיקת Ortho ---
            if ortho_ready:
                o_search_pattern = os.path.join(o_folder, "**", sheet_id + "*.tif")
                o_matches = glob.glob(o_search_pattern, recursive=True)
                if o_matches:
                    try:
                        with rasterio.open(o_matches[0]) as src:
                            o_raster_bound = src.bounds
                            # מילוי רשומת מידע טכני
                            ortho_info_records.append(extract_raster_info(sheet_id, src, sheet_geom))


                            if src.crs and src.crs.to_epsg() != 2039:
                                counters["1001"] += 1
                                all_current_errors.append("1001: Wrong projection")
                            if src.dtypes[0] != 'uint8':
                                counters["1002_type"] += 1
                                all_current_errors.append("1002: Wrong type")
                            if not (3 <= src.count <= 4):
                                counters["1002_bands"] += 1
                                all_current_errors.append("1002: Wrong bands")
                            if abs(src.res[0] - float(o_res_val)/100) > o_tolerance:
                                counters["1003"] += 1
                                all_current_errors.append("1003: Wrong resolution")
                    except:
                        counters["1002_fmt"] += 1
                        all_current_errors.append("1002: Wrong format")


            # --- בדיקת DSM ---
            if dsm_ready:
                d_search_pattern = os.path.join(d_folder, "**", sheet_id + "*.tif")
                d_matches = glob.glob(d_search_pattern, recursive=True)
                if d_matches:
                    try:
                        with rasterio.open(d_matches[0]) as src:
                            d_raster_bound = src.bounds
                            dsm_info_records.append(extract_raster_info(sheet_id, src, sheet_geom))


                            if 'float' not in str(src.dtypes[0]):
                                counters["1004_type"] += 1
                                all_current_errors.append("1004: Wrong type")
                            if src.count != 1:
                                counters["1004_bands"] += 1
                                all_current_errors.append("1004: Wrong bands")
                            if abs(src.res[0] - float(d_res_val)/100) > d_tolerance:
                                counters["1005"] += 1
                                all_current_errors.append("1005: Wrong resolution")
                    except:
                        counters["1004_fmt"] += 1
                        all_current_errors.append("1004: Wrong format")


            # --- בדיקת 1006 (חפיפה והתאמה) ---
            if ortho_ready and dsm_ready:
                o_exists = len(glob.glob(os.path.join(o_folder, sheet_id + "*.tif"))) > 0
                # print o_exists
                if not o_exists or not d_matches:
                    counters["1006"] += 1
                    all_current_errors.append("1006: Missing match")
                elif o_raster_bound and d_raster_bound:
                    # בדיקת חיתוך גיאומטרי (האם ה-Extents חופפים)
                    if (o_raster_bound.left >= d_raster_bound.right or o_raster_bound.right <= d_raster_bound.left or
                        o_raster_bound.bottom >= d_raster_bound.top or o_raster_bound.top <= d_raster_bound.bottom):
                        counters["1006"] += 1
                        all_current_errors.append("1006: No overlap")


            # כתיבת שגיאות לרשימה
            if all_current_errors:
                error_records.append({'Sheet': sheet_id, 'Error': "; ".join(all_current_errors), 'geometry': sheet_geom})


        # --- כתיבת סיכום CSV
        with open(summary_csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(["Check summary:"])
            w.writerow([f"1001 - {counters['1001']} images found with wrong projection"])
            w.writerow([f"1002 - {counters['1002_fmt']} ortho images found with wrong format"])
            w.writerow([f"1002 - {counters['1002_type']} ortho images found with wrong image type"])
            w.writerow([f"1002 - {counters['1002_bands']} ortho images found with wrong bands"])
            w.writerow([f"1003 - {counters['1003']} ortho images found with wrong resolution"])
            w.writerow([f"1004 - {counters['1004_fmt']} dsm images found with wrong format"])
            w.writerow([f"1004 - {counters['1004_type']} dsm images found with wrong image type"])
            w.writerow([f"1004 - {counters['1004_bands']} dsm images found with wrong bands"])
            w.writerow([f"1005 - {counters['1005']} dsm images found with wrong resolution"])
            w.writerow([f"1006 - there are {counters['1006']} mismatching dsm to ortho images"])


        # --- שמירת שכבות (GeoJSON) ---
       # --- שמירת שכבות כ-SHAPEFILE ---
        if error_records:
            gpd.GeoDataFrame(error_records, crs="EPSG:2039").to_file(os.path.join(output_base, "QA_Errors_Map.shp"))


        if ortho_info_records:
            gpd.GeoDataFrame(ortho_info_records, crs="EPSG:2039").to_file(os.path.join(output_base, "Ortho_Info_1000.shp"))
           
        if dsm_info_records:
            gpd.GeoDataFrame(dsm_info_records, crs="EPSG:2039").to_file(os.path.join(output_base, "DSM_Info_1000.shp"))
        progress_info.update({"percent": 100, "message": "הבדיקה הסתיימה בהצלחה!"})
        return jsonify({'status': 'success', 'message': f'הבדיקה הושלמה.\nקבצי ה-CSV והמפות נוצרו בתיקיית המסמכים: {output_base}'})


    except Exception as e:
        return jsonify({'status': 'error', 'message': f'שגיאה במערכת: {str(e)}'})


if __name__ == '__main__':
    app.run(debug=True)