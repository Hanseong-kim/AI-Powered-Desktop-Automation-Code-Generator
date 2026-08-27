// ============================================================
// Medflow mock-app shared data module (Windows 2003 / IE6-safe JScript)
// Loaded via <script src="shared/data.js"> by every window (Login excluded
// — it doesn't need patient data). Each window regenerates the SAME
// synthetic dataset independently (fixed RNG seed, no cross-process IPC) so
// a patientId picked in one window resolves to the same person in another.
// ============================================================

// --- DOM helpers (IE6 safe) ---
function $(id){ return document.getElementById(id); }

function addEvent(el, evt, fn){
  if (!el) return;
  if (el.attachEvent) el.attachEvent('on' + evt, fn);
  else if (el.addEventListener) el.addEventListener(evt, fn, false);
  else el['on' + evt] = fn;
}

function setText(el, txt){
  if (!el) return;
  if (typeof el.innerText !== 'undefined') el.innerText = txt;
  else el.textContent = txt;
}

function trim(s){
  return String(s).replace(/^\s+|\s+$/g,'');
}

function toLower(s){
  return String(s).toLowerCase();
}

// --- Seeded RNG (LCG) ---
// Fixed constant seed (NOT date-based) so every one of the 4 windows
// regenerates byte-identical patient/encounter data with no IPC, and
// recorded selectors don't rot overnight.
var __seed = 1;
function initSeed(){
  __seed = 20260827;
}
function rand(){
  __seed = (__seed * 1103515245 + 12345) & 0x7fffffff;
  return __seed / 0x7fffffff;
}
function r(min, max){
  return Math.floor(rand() * (max - min + 1)) + min;
}
function pick(arr){
  return arr[Math.floor(rand() * arr.length)];
}

function pad2(n){
  n = String(n);
  return (n.length < 2) ? ("0" + n) : n;
}
function formatDate(d){
  return pad2(d.getDate()) + "/" + pad2(d.getMonth()+1) + "/" + d.getFullYear();
}
function formatTime(d){
  var h = d.getHours();
  var m = pad2(d.getMinutes());
  var s = pad2(d.getSeconds());
  var ampm = (h >= 12) ? "PM" : "AM";
  h = h % 12;
  if (h === 0) h = 12;
  return h + ":" + m + ":" + s + " " + ampm;
}
function formatTimeShort(d){
  var h = d.getHours();
  var m = pad2(d.getMinutes());
  var ampm = (h >= 12) ? "PM" : "AM";
  h = h % 12;
  if (h === 0) h = 12;
  return h + ":" + m + " " + ampm;
}
function dateAddDays(base, days){
  var d = new Date(base.getTime());
  d.setDate(d.getDate() + days);
  return d;
}

// --- App Data ---
var FIRST = ["James","Mary","Robert","Patricia","John","Jennifer","Michael","Linda","David","Elizabeth","William","Barbara","Richard","Susan","Joseph","Jessica","Thomas","Sarah","Charles","Karen","Daniel","Nancy","Matthew","Lisa","Anthony","Betty","Mark","Sandra","Paul","Ashley","Steven","Kimberly","Andrew","Donna","Kenneth","Emily","George","Michelle","Joshua","Carol","Kevin","Amanda","Brian","Melissa"];
var LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson","Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores","Green","Adams","Nelson","Baker","Hall","Rivera","Campbell","Mitchell","Carter","Roberts"];
var PROVIDERS = ["William H Bartlett MD","S. Patel MD","L. Chen MD","A. Rodriguez MD","K. Nguyen MD"];
var TECHS = ["CitrusBits","E. Johnson","M. Flores","T. Nguyen","R. Patel"];
var FACILITIES = ["TC1","TC2","East"];

var GLAUCOMA_DX = ["Primary Open Angle Glaucoma (POAG)","Normal Tension Glaucoma (NTG)","Pseudoexfoliative Glaucoma (PXF)","Primary Angle Closure Glaucoma (PACG)","Ocular Hypertension (OHTN)"];
var GLAUCOMA_STAGE = ["Mild","Moderate","Severe"];
var LATERALITY = ["OD","OS","OU"];
var RETINA_DX = ["Diabetic Retinopathy","Wet AMD","Dry AMD","Retinal Tear","Vitreous Hemorrhage","Macular Edema"];
var PEDS_REASON = ["Amblyopia check","Strabismus follow-up","Myopia progression","School vision exam","Red reflex check"];
var OPTICAL_TASK = ["Frame selection","Lens order status","Glasses pickup","Rx verification","Warranty adjustment"];
var CL_TASK = ["Soft lens fit","Toric lens eval","RGP follow-up","Dispense visit","Dry eye CL check"];
var REFR_REASON = ["LASIK evaluation","PRK follow-up","Refractive consult","Topo review","Dry eye pre-op"];
var MEDS = ["Latanoprost qHS","Bimatoprost qHS","Timolol BID","Brimonidine TID","Dorzolamide TID","Dorzolamide/Timolol BID","Netarsudil qHS","Pilocarpine QID"];

function randomPhone(){
  return "(" + r(200,989) + ") " + r(200,989) + "-" + r(1000,9999);
}
function randomMRN(){
  return String(r(10000000, 99999999));
}
function randomDOB(age){
  var today = new Date();
  var year = today.getFullYear() - age;
  var month = r(0,11);
  var day = r(1,28);
  return new Date(year, month, day);
}
function chooseRxStatus(){
  var x = rand();
  if (x < 0.10) return "pending";
  if (x < 0.18) return "refill";
  if (x < 0.23) return "change";
  if (x < 0.33) return "needsigning";
  return "none";
}

var DATA = { patients: [], encounters: {} };

function generatePatients(){
  var patients = [];
  var counter = 0;

  // Glaucoma-heavy registry
  var glaucomaCount = 22;
  var i;
  for (i=0;i<glaucomaCount;i++){
    counter++;
    var age = r(45, 88);
    var sex = pick(["F","M"]);
    var first = pick(FIRST);
    var last = pick(LAST);
    var name = last + ", " + first;
    var facility = pick(FACILITIES);
    var dept = "Glaucoma";
    var dx = pick(GLAUCOMA_DX);
    var stage = pick(GLAUCOMA_STAGE);
    var laterality = pick(LATERALITY);

    var baseIOP = (stage=="Mild") ? r(12,20) : (stage=="Moderate") ? r(14,24) : r(16,30);
    var iopOD = baseIOP + r(-3,3);
    var iopOS = baseIOP + r(-3,3);
    if (iopOD < 8) iopOD = 8; if (iopOD > 35) iopOD = 35;
    if (iopOS < 8) iopOS = 8; if (iopOS > 35) iopOS = 35;

    var target = (stage=="Mild") ? r(14,18) : (stage=="Moderate") ? r(12,16) : r(10,14);

    var cctOD = r(480, 620);
    var cctOS = r(480, 620);

    var cdBase = (stage=="Mild") ? (rand()*0.2+0.4) : (stage=="Moderate") ? (rand()*0.2+0.6) : (rand()*0.2+0.75);
    var cdOD = Math.round(cdBase*100)/100;
    var cdOS = Math.round(((stage=="Mild") ? (rand()*0.2+0.4) : (stage=="Moderate") ? (rand()*0.2+0.6) : (rand()*0.2+0.75))*100)/100;

    var vfOD = (stage=="Mild") ? -(rand()*4+1) : (stage=="Moderate") ? -(rand()*6+6) : -(rand()*10+12);
    var vfOS = (stage=="Mild") ? -(rand()*4+1) : (stage=="Moderate") ? -(rand()*6+6) : -(rand()*10+12);
    vfOD = Math.round(vfOD*10)/10; vfOS = Math.round(vfOS*10)/10;

    var rnflOD = (stage=="Mild") ? r(80,105) : (stage=="Moderate") ? r(65,90) : r(45,75);
    var rnflOS = (stage=="Mild") ? r(80,105) : (stage=="Moderate") ? r(65,90) : r(45,75);

    var medsCount = (stage=="Mild") ? r(0,2) : (stage=="Moderate") ? r(1,3) : r(2,4);
    var medsArr = [];
    var j;
    for (j=0;j<medsCount;j++){
      medsArr[medsArr.length] = pick(MEDS);
    }
    // de-duplicate
    var medsUnique = [];
    for (j=0;j<medsArr.length;j++){
      var found = false;
      var k;
      for (k=0;k<medsUnique.length;k++){
        if (medsUnique[k] == medsArr[j]) { found = true; break; }
      }
      if (!found) medsUnique[medsUnique.length] = medsArr[j];
    }
    var meds = (medsUnique.length) ? medsUnique.join("; ") : "None on file";

    var lastVisit = dateAddDays(new Date(), -r(7,120));
    var nextAppt = dateAddDays(new Date(), r(7,140));

    patients[patients.length] = {
      id: "P" + counter,
      mrn: randomMRN(),
      name: name,
      first: first,
      last: last,
      sex: sex,
      age: age,
      dob: randomDOB(age),
      phone: randomPhone(),
      facility: facility,
      department: dept,
      provider: pick(PROVIDERS),
      technician: pick(TECHS),
      diagnosis: dx,
      stage: stage,
      laterality: laterality,
      iopOD: iopOD,
      iopOS: iopOS,
      targetIOP: target,
      cctOD: cctOD,
      cctOS: cctOS,
      cdOD: cdOD,
      cdOS: cdOS,
      vfMD_OD: vfOD,
      vfMD_OS: vfOS,
      rnflOD: rnflOD,
      rnflOS: rnflOS,
      meds: meds,
      lastVisit: lastVisit,
      nextAppt: nextAppt,
      rxStatus: chooseRxStatus(),
      notes: "Continue current regimen; recheck IOP at next visit."
    };
  }

  // Retina
  var retinaCount = 10;
  for (i=0;i<retinaCount;i++){
    counter++;
    var age2 = r(35,90);
    var sex2 = pick(["F","M"]);
    var first2 = pick(FIRST);
    var last2 = pick(LAST);
    var facility2 = pick(FACILITIES);
    var dx2 = pick(RETINA_DX);
    var name2 = last2 + ", " + first2;

    patients[patients.length] = {
      id: "P" + counter,
      mrn: randomMRN(),
      name: name2,
      first: first2,
      last: last2,
      sex: sex2,
      age: age2,
      dob: randomDOB(age2),
      phone: randomPhone(),
      facility: facility2,
      department: "Retina",
      provider: pick(PROVIDERS),
      technician: pick(TECHS),
      diagnosis: dx2,
      stage: pick(["Stable","Active","New"]),
      laterality: pick(LATERALITY),
      iopOD: r(10,22),
      iopOS: r(10,22),
      targetIOP: 0,
      cctOD: r(500,600),
      cctOS: r(500,600),
      cdOD: "",
      cdOS: "",
      vfMD_OD: "",
      vfMD_OS: "",
      rnflOD: "",
      rnflOS: "",
      meds: pick(["AREDS2","None on file","Prednisolone taper","Artificial tears"]),
      lastVisit: dateAddDays(new Date(), -r(5,90)),
      nextAppt: dateAddDays(new Date(), r(7,90)),
      rxStatus: chooseRxStatus(),
      notes: "OCT macula performed; review with provider."
    };
  }

  // Pediatrics
  var pedsCount = 10;
  for (i=0;i<pedsCount;i++){
    counter++;
    var age3 = r(3,13);
    var sex3 = pick(["F","M"]);
    var first3 = pick(["Olivia","Emma","Ava","Sophia","Mia","Noah","Liam","Ethan","Lucas","Mason","Ella","Grace","Zoe","Leo","Jack"]);
    var last3 = pick(LAST);
    var facility3 = pick(FACILITIES);
    var name3 = last3 + ", " + first3;

    patients[patients.length] = {
      id: "P" + counter,
      mrn: randomMRN(),
      name: name3,
      first: first3,
      last: last3,
      sex: sex3,
      age: age3,
      dob: randomDOB(age3),
      phone: randomPhone(),
      facility: facility3,
      department: "Pediatrics",
      provider: pick(PROVIDERS),
      technician: pick(TECHS),
      diagnosis: pick(PEDS_REASON),
      stage: pick(["Routine","Follow-up","New"]),
      laterality: "",
      iopOD: r(8,18),
      iopOS: r(8,18),
      targetIOP: 0,
      cctOD: "",
      cctOS: "",
      cdOD: "",
      cdOS: "",
      vfMD_OD: "",
      vfMD_OS: "",
      rnflOD: "",
      rnflOS: "",
      meds: "None on file",
      lastVisit: dateAddDays(new Date(), -r(10,220)),
      nextAppt: dateAddDays(new Date(), r(7,160)),
      rxStatus: "none",
      notes: "Peds screening protocols; ensure cycloplegic refraction if indicated."
    };
  }

  // Optical
  var opticalCount = 8;
  for (i=0;i<opticalCount;i++){
    counter++;
    var age4 = r(16,85);
    var sex4 = pick(["F","M"]);
    var first4 = pick(FIRST);
    var last4 = pick(LAST);
    var facility4 = pick(FACILITIES);
    var name4 = last4 + ", " + first4;

    patients[patients.length] = {
      id: "P" + counter,
      mrn: randomMRN(),
      name: name4,
      first: first4,
      last: last4,
      sex: sex4,
      age: age4,
      dob: randomDOB(age4),
      phone: randomPhone(),
      facility: facility4,
      department: "Optical",
      provider: pick(PROVIDERS),
      technician: pick(TECHS),
      diagnosis: pick(OPTICAL_TASK),
      stage: pick(["Pending","In progress","Ready","Completed"]),
      laterality: "",
      iopOD: "",
      iopOS: "",
      targetIOP: 0,
      cctOD: "",
      cctOS: "",
      cdOD: "",
      cdOS: "",
      vfMD_OD: "",
      vfMD_OS: "",
      rnflOD: "",
      rnflOS: "",
      meds: "—",
      lastVisit: dateAddDays(new Date(), -r(1,40)),
      nextAppt: dateAddDays(new Date(), r(1,21)),
      rxStatus: "none",
      notes: "Optical order workflow item."
    };
  }

  // Contact Lenses
  var clCount = 8;
  for (i=0;i<clCount;i++){
    counter++;
    var age5 = r(14,65);
    var sex5 = pick(["F","M"]);
    var first5 = pick(FIRST);
    var last5 = pick(LAST);
    var facility5 = pick(FACILITIES);
    var name5 = last5 + ", " + first5;

    patients[patients.length] = {
      id: "P" + counter,
      mrn: randomMRN(),
      name: name5,
      first: first5,
      last: last5,
      sex: sex5,
      age: age5,
      dob: randomDOB(age5),
      phone: randomPhone(),
      facility: facility5,
      department: "Contact Lenses",
      provider: pick(PROVIDERS),
      technician: pick(TECHS),
      diagnosis: pick(CL_TASK),
      stage: pick(["New fit","Follow-up","Dispense"]),
      laterality: "",
      iopOD: r(10,22),
      iopOS: r(10,22),
      targetIOP: 0,
      cctOD: "",
      cctOS: "",
      cdOD: "",
      cdOS: "",
      vfMD_OD: "",
      vfMD_OS: "",
      rnflOD: "",
      rnflOS: "",
      meds: pick(["Artificial tears","None on file","Pataday PRN"]),
      lastVisit: dateAddDays(new Date(), -r(5,120)),
      nextAppt: dateAddDays(new Date(), r(7,70)),
      rxStatus: chooseRxStatus(),
      notes: "CL workflow item; confirm brand/BC/DIA."
    };
  }

  // Refractive
  var refrCount = 6;
  for (i=0;i<refrCount;i++){
    counter++;
    var age6 = r(20,55);
    var sex6 = pick(["F","M"]);
    var first6 = pick(FIRST);
    var last6 = pick(LAST);
    var facility6 = pick(FACILITIES);
    var name6 = last6 + ", " + first6;

    patients[patients.length] = {
      id: "P" + counter,
      mrn: randomMRN(),
      name: name6,
      first: first6,
      last: last6,
      sex: sex6,
      age: age6,
      dob: randomDOB(age6),
      phone: randomPhone(),
      facility: facility6,
      department: "Refractive",
      provider: pick(PROVIDERS),
      technician: pick(TECHS),
      diagnosis: pick(REFR_REASON),
      stage: pick(["Consult","Pre-op","Post-op"]),
      laterality: "",
      iopOD: r(10,20),
      iopOS: r(10,20),
      targetIOP: 0,
      cctOD: r(490,610),
      cctOS: r(490,610),
      cdOD: "",
      cdOS: "",
      vfMD_OD: "",
      vfMD_OS: "",
      rnflOD: "",
      rnflOS: "",
      meds: pick(["Artificial tears","Steroid taper","None on file"]),
      lastVisit: dateAddDays(new Date(), -r(5,90)),
      nextAppt: dateAddDays(new Date(), r(7,90)),
      rxStatus: chooseRxStatus(),
      notes: "Refractive workflow item."
    };
  }

  // Workup patients are drawn from other departments (Workup is treated as a workflow list)
  return patients;
}


// Generate encounters for each patient (Patient Data -> Encounters column)
function generateEncounters(patients){
  var encounters = {};
  var descriptions = ["Follow-Up", "Visit", "Initial Consultation", "Post-Op", "Emergency"];
  var facilities = ["My DemoPractice", "Bethesda", "TC1", "TC2", "East"];

  var i;
  for (i=0;i<patients.length;i++){
    var p = patients[i];
    var numEnc = r(2, 8);
    var list = [];
    var baseId = 800 + r(0, 120);

    var j;
    for (j=0;j<numEnc;j++){
      var daysAgo = r(1, 365);
      var encDate = dateAddDays(new Date(), -daysAgo);
      list[list.length] = {
        id: baseId + j,
        patientId: p.id,
        providerName: pick(PROVIDERS),
        facilityName: pick(facilities),
        date: encDate,
        description: pick(descriptions),
        hasSummary: (rand() > 0.3) ? 1 : 0
      };
    }

    // Sort by date descending (most recent first)
    list.sort(function(a,b){ return b.date - a.date; });
    encounters[p.id] = list;
  }
  return encounters;
}

function findPatientById(pid){
  if (!pid) return null;
  var i;
  for (i=0;i<DATA.patients.length;i++){
    if (DATA.patients[i].id == pid) return DATA.patients[i];
  }
  return null;
}

function getEncountersForPatient(patientId){
  if (!DATA.encounters) DATA.encounters = {};
  if (!DATA.encounters[patientId]) DATA.encounters[patientId] = [];
  return DATA.encounters[patientId];
}

function findEncounterById(patientId, encId){
  var list = getEncountersForPatient(patientId);
  var i;
  for (i=0;i<list.length;i++){
    if (String(list[i].id) == String(encId)) return list[i];
  }
  return null;
}

// Regenerate the full synthetic dataset from the fixed seed. Call once per
// window on load — every window ends up with byte-identical DATA.patients /
// DATA.encounters with no cross-process IPC.
function initMedflowData(){
  initSeed();
  DATA.patients = generatePatients();
  DATA.encounters = generateEncounters(DATA.patients);
}
