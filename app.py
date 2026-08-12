import os
from dotenv import load_dotenv
load_dotenv()
from flask import Flask,request,render_template,send_file
from werkzeug.utils import secure_filename#It converts unsafe filenames into safe ones.
import folium
import exifread
import datetime
from pyproj import Transformer
import requests
from forensics import run_full_analysis, perform_ela, detect_faces

app=Flask(__name__)
UPLOAD_FOLDER='uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)#creates upload folder automatically
app.config['UPLOAD_FOLDER']=UPLOAD_FOLDER

def exif_warning_message(tags,filepath):#tags-exif metadata dictionary
    warnings=[]
    exif_date=tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    file_mtime=os.path.getmtime(filepath)
    if exif_date:
        exif_date_str = str(exif_date)
        exif_datetime=None#initial value
        try:
            exif_datetime=datetime.datetime.strptime(exif_date_str, "%Y:%m:%d %H:%M:%S")
        except Exception:
            warnings.append("EXIF date/time is missing or not in a standard format.")
        if exif_datetime:
            file_datetime=datetime.datetime.fromtimestamp(file_mtime)
            delta = abs((exif_datetime - file_datetime).total_seconds())
            if delta > 3600 * 24:
                warnings.append("EXIF timestamp and file modification time differ significantly. Possible tampering.")

    #image editing s/w
    software=tags.get("Image Software")
    if software and any(s in str(software).lower() for s in ["photoshop","editor","gimp","snapseed"]):
        warnings.append(f"EXIF indicates use of editing software: {software}")
    #suspicious make/model
    make, model=tags.get("Image Make"), tags.get("Image Model")
    if make and model and("fake" in str(make).lower() or "fake" in str(model).lower()):
        warnings.append(f"Camera Make/Model looks suspicious: {make} / {model}")

     #all zero gps
    gpslat=tags.get('GPS GPSLatitude')
    if gpslat and str(gpslat).replace(" ", "").replace(",", "") == "0/1 0/1 0/1":
        warnings.append("GPS coordinates are all zeros (uninitialized or faked).")

    #gps present but no Make/Model
    if tags.get('GPS GPSLatitude') and not (make and model):
        warnings.append("GPS is present, but no camera Make/Model recorded. Possible manipulation.")

    return warnings  

def get_exif_gps(image_path):
    with open(image_path,'rb') as f:#opens image in binary format in variable f
        tags=exifread.process_file(f,details=False)
        if 'GPS GPSLatitude' in tags and 'GPS GPSLongitude' in tags:
            lat=dms_to_decimal(tags['GPS GPSLatitude'],tags.get('GPS GPSLatitudeRef'))#first tags get latitide value tags.getmso on gets direction N&S
            lon=dms_to_decimal(tags['GPS GPSLongitude'],tags.get('GPS GPSLongitudeRef'))
            return lat,lon
    return None,None

def dms_to_decimal(dms,ref):
    d,m,s=[float(x.num)/float(x.den) for x in dms.values]
    decimal=d + m/60 +s/3600 
    if ref and ref.values in ['S','W']:
        decimal=-decimal
    return decimal

def create_map(lat,lon,map_path):
    m=folium.Map(location=[lat,lon],zoom_start=15)
    folium.Marker([lat,lon],popup="Photo Location",tooltip="Photo").add_to(m)
    m.save(map_path)

def wgs84_to_utm(lat, lon):
    if lat is None or lon is None:
        return None, None, None
    zone=int((lon + 180) / 6) + 1
    hemisphere='north' if lat >= 0 else 'south'
    transformer=Transformer.from_crs("epsg:4326", f"+proj=utm +zone={zone} +{hemisphere} +datum=WGS84 +units=m +no_defs", always_xy=True)#not done
    utm_x, utm_y=transformer.transform(lon, lat)
    return utm_x, utm_y, f"{zone}{hemisphere[0].upper()}"

def valid_region(lat,lon):
    try:
        res=requests.get(f'https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json', headers={'User-Agent': 'Browser'})#lat+long -> real address
        if res.status_code==200:#200 means successfull
            data=res.json()
            if 'address' in data:
                country=data['address'].get('country')
                return country
        return None
    except Exception:
        return None


    #no sure probab of 90% just low med and high
    

@app.route('/', methods=['GET', 'POST'])#decorator in flask on homepage http://127.0.0.1:5000/   allows two req get and post
def upload_image():
    warnings=[]
    if request.method=='POST':
        if 'file' not in request.files:#check if browser actually sent a file
            return render_template('upload.html', error="No file part")
        file=request.files['file']
        if file.filename=="":
            return render_template('upload.html', error="No selected file")
        
        filename=secure_filename(file.filename)
        filepath=os.path.join(app.config['UPLOAD_FOLDER'], filename)#os.path.join() Safely joins folder + filename.
        file.save(filepath)

        with open(filepath,'rb') as f:
            tags=exifread.process_file(f,details=False)
        warnings=exif_warning_message(tags,filepath)
        map_filename = None
        map_path = None
        lat,lon=get_exif_gps(filepath)
        if lat and lon:
            map_filename=f"{filename}_map.html"
            map_path=os.path.join(app.config['UPLOAD_FOLDER'], map_filename)
            create_map(lat,lon,map_path)

        ela_path = perform_ela(filepath)
        num_faces = detect_faces(filepath)
        report = run_full_analysis(filepath, tags)
        forensics = report.to_dict()
        forensics["ela_image"] = os.path.basename(ela_path)
        forensics["faces_detected"] = num_faces

        utm_x, utm_y, utm_zone = wgs84_to_utm(lat, lon)
        country = valid_region(lat, lon) if lat and lon else None

        return render_template(
            'result.html',
            map_file=map_filename,
            lat=lat, lon=lon,
            warnings=warnings,
            forensics=forensics,
            photo_filename=filename,
            utm_x=utm_x, utm_y=utm_y, utm_zone=utm_zone,
            country=country,
        )

    return render_template('upload.html')

@app.route('/uploads/<map_file>')#creates another url
def serve_map(map_file):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], map_file))#sends html file to browser

if __name__=='__main__':
    app.run(debug=True)


