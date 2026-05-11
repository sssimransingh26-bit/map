import os
from flask import Flask,request,render_template,send_file
from werkzeug.utils import secure_filename#It converts unsafe filenames into safe ones.
from PIL import Image,ImageChops,ImageEnhance
import folium
import exifread
import datetime
import numpy as np
import cv2
from skimage.util import view_as_windows#divide image into blocks
import imagehash

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

def detect_faces(image_path):
    face_cascade=cv2.CascadeClassifier(cv2.data.haarcascades+ "haarcascade_frontalface_default.xml")
    img=cv2.imread(image_path)
    if img is None:
        return 0
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    faces=face_cascade.detectMultiScale(gray,1.1,4)
    return len(faces)
#cv2.CascadeClassifier creates object that can detect faces
#cv2.data.haarcascades built in folderpath containing opencv models
#haarcascade_frontalface_default.xml pretrained harcasscade model for front facing human

def perform_ela(image_path,scale=15):
    img=Image.open(image_path).convert('RGB')
    temp_filename=image_path+"_temp_ela.jpg"
    img.save(temp_filename, 'JPEG', quality=90)
    #jpeg lose small amount of info every save- edited regions compress differently than untouched area so ela highlights them
    compressed=Image.open(temp_filename)
    diff=ImageChops.difference(img,compressed)
    pixel_ranges=diff.getextrema()#gets min max value for each color
    max_diff=max([ex[1] for ex in pixel_ranges])
    if max_diff==0:
        max_diff=1
    diff=ImageEnhance.Brightness(diff).enhance(scale)
    ela_path=image_path+"_ELA.png"
    diff.save(ela_path)
    os.remove(temp_filename)
    return ela_path


def analyze_noise(img_path,block_size=32):
    img=cv2.imread(img_path,cv2.IMREAD_GRAYSCALE)
    blocks=view_as_windows(img, (block_size, block_size), step=block_size)
    std_map = np.std(blocks, axis=(2,3))
    min_std,max_std=np.min(std_map),np.max(std_map)
    if max_std-min_std>15:
        return f"Noise inconsistency detected (std ∆={max_std-min_std:.2f}). Possible manipulation."
    else :
        return "Noise levels are consistent across the image."
    

def analyze_clone(img_path,block_size=32,stride=16):
       img=Image.open(img_path).convert("L")#grayscale l means luminance b*w used with pillow
       w,h=img.size
       hashes={}
       for y in range(0,h-block_size,stride):
           for x in range(0,w-block_size,stride):
               region=img.crop((x,y,x+block_size,y+block_size))
               hsh=imagehash.average_hash(region)#creates compact hash
               hashes.setdefault(str(hsh),[]).append((x,y))
       duplicate_blocks=[locs for locs in hashes.values() if len(locs)>1]
       if duplicate_blocks:
           return f"Warning: Possible cloned (copy-move) regions found in {len(duplicate_blocks)} area(s)."
       return "No strong evidence of cloned regions (copy-move)."

def analyze_edge_artifacts(img_path,block_size=32):
    if img_path is None:
        return "Invalid image"
    img=cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    edges=cv2.Canny(img,100,200)#canny edge detection
    edge_blocks=view_as_windows(edges, (block_size, block_size), step=block_size)
    block_means=np.mean(edge_blocks, axis=(2,3))
    std=np.std(block_means)
    if std>15:  # rough heuristic
        return f"Edge artifact inconsistency detected (std={std:.2f}). Possible splicing/overlay."
    return "Edge structures are consistent."


def detect_ai_generated(tags,img_path):
    ai_tools=["stable diffusion", "midjourney", "dall-e", "artbreeder", "generative", "dream", "diffusion", "wombo", "adobe firefly"]
    software=str(tags.get("Software", "") or tags.get("Image Software", "")).lower()
    for tool in ai_tools:
        if tool in software:
            return f"EXIF software suggests AI-generated art tool detected: '{tool}'"
    comment=str(tags.get("UserComment", "")).lower()
    if "negative_prompt" in comment or "steps:" in comment:
        return "Confirmed: Stable Diffusion generation parameters found in UserComment."
    img=Image.open(img_path)
    if (not tags) or (len(tags) < 5 and img.width > 1024 and img.height > 1024):
        return "Suspicious: Large image with nearly no metadata. Could be AI-generated or heavily edited."
    return None


def analyze_image_for_forensics(image_path,tags):
    ela_path=perform_ela(image_path)
    num_faces=detect_faces(image_path)
    findings=[]
    findings.append(f"Faces detected: {num_faces}")#face analysis
    findings.append(analyze_noise(image_path))#noise
    findings.append(analyze_clone(image_path))#clone
    findings.append(analyze_edge_artifacts(image_path))#edges
    ai_result=detect_ai_generated(tags,image_path)
    if ai_result:
        findings.append(ai_result)
    findings.append("Check ELA below: white/bright = possible manipulation.")
    return{
        "ela_image":os.path.basename(ela_path),#removes folder path frontend only needs filename not path
        "faces_detected": num_faces,
        "summary": findings
    }


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
        
        forensics=analyze_image_for_forensics(filepath, tags)
        return render_template(
            'result.html',
            map_file=map_filename,
            lat=lat, lon=lon,
            warnings=warnings,
            forensics=forensics,
            image_filename=filename
        )
    return render_template('upload.html')

@app.route('/uploads/<map_file>')#creates another url
def serve_map(map_file):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], map_file))#sends html file to browser

if __name__=='__main__':
    app.run(debug=True)


