export const officialSourceUrls = {
  onlineHelpRoot: "https://content.helpme-codesys.com/",
  examplesStartPage: "https://content.helpme-codesys.com/en/CODESYS%20Examples/_ex_start_page.html",
  projectApplication:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_f_project_application.html",
  developmentSystem:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_development_system.html",
  developmentSystemProduct: "https://us.codesys.com/products/engineering/development-system/",
  programmingInSt:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_programming_in_st.html",
  programmingLanguages:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_struct_reference_programming_languages_and_editors.html",
  pou: "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_f_obj_pou.html",
  functionBlock:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_function_block.html",
  function:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_function.html",
  method:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_method.html",
  property:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_property.html",
  dut: "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_dut.html",
  enum: "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_datatype_enum.html",
  gvl: "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_gvl.html",
  gvlNamespaces:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_using_namespaces_gvl.html",
  persistentVariables:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_preserve_data_with_persistent_variables.html",
  operators:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_struct_reference_operators.html",
  functionBlockCall:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_st_fb_call.html",
  objectOriented:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_f_object_oriented_programming.html",
  timeConstants:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_operands_constant_time.html",
  ton: "https://content.helpme-codesys.com/en/libs/Standard/3.5.17.0/Timer/TON.html",
  tof: "https://content.helpme-codesys.com/en/libs/Standard/Current/Timer/TOF.html",
  tp: "https://content.helpme-codesys.com/en/libs/Standard/Current/Timer/TP.html",
  standardLibrary: "https://content.helpme-codesys.com/en/libs/Standard/3.5.17.0/index.html",
  ctu: "https://content.helpme-codesys.com/en/libs/Standard/Current/Counter/CTU.html",
  ctd: "https://content.helpme-codesys.com/en/libs/Standard/Current/Counter/CTD.html",
  rTrig: "https://content.helpme-codesys.com/en/libs/Standard/Current/Trigger/R_TRIG.html",
  fTrig: "https://content.helpme-codesys.com/en/libs/Standard/Current/Trigger/F_TRIG.html",
  visualization:
    "https://content.helpme-codesys.com/en/CODESYS%20Visualization/_visu_start_page.html",
  fieldbus: "https://content.helpme-codesys.com/en/CODESYS%20Fieldbus/_fieldbus_start_page.html",
  softmotion: "https://content.helpme-codesys.com/en/CODESYS%20SoftMotion/_sm_start_page.html",
  taskMonitoring:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_task_config_monitor.html",
  taskConfiguration:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_f_reference_task.html",
  multicore:
    "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_f_multicore.html"
} as const;

export interface OfficialSource {
  id: string;
  title: string;
  url: string;
  tags: string[];
}

export const officialSources: OfficialSource[] = [
  {
    id: "official-online-help-root",
    title: "CODESYS Online Help",
    url: officialSourceUrls.onlineHelpRoot,
    tags: ["online-help", "official-docs", "codesys", "documentation"]
  },
  {
    id: "official-codesys-examples",
    title: "CODESYS Examples",
    url: officialSourceUrls.examplesStartPage,
    tags: [
      "examples",
      "sample-projects",
      "development-system",
      "fieldbus",
      "visualization",
      "softmotion",
      "cnc",
      "robotics"
    ]
  },
  {
    id: "official-development-system",
    title: "CODESYS Development System Overview",
    url: officialSourceUrls.developmentSystem,
    tags: ["overview", "development-system", "v3"]
  },
  {
    id: "official-project-application",
    title: "Project And Application",
    url: officialSourceUrls.projectApplication,
    tags: ["project", "application", "device-tree", "architecture"]
  },
  {
    id: "official-product-development-system",
    title: "CODESYS Development System Product Page",
    url: officialSourceUrls.developmentSystemProduct,
    tags: ["overview", "development-system", "product"]
  },
  {
    id: "official-structured-text",
    title: "Programming in Structured Text",
    url: officialSourceUrls.programmingInSt,
    tags: ["structured-text", "st", "syntax", "programming"]
  },
  {
    id: "official-programming-languages",
    title: "Programming Languages and Editors",
    url: officialSourceUrls.programmingLanguages,
    tags: ["editors", "structured-text", "fbd", "ld", "sfc", "cfc"]
  },
  {
    id: "official-pou",
    title: "Object: POU",
    url: officialSourceUrls.pou,
    tags: ["pou", "program", "function", "function-block"]
  },
  {
    id: "official-function-block",
    title: "Object: Function Block",
    url: officialSourceUrls.functionBlock,
    tags: ["function-block", "fb", "pou", "oop"]
  },
  {
    id: "official-function",
    title: "Object: Function",
    url: officialSourceUrls.function,
    tags: ["function", "pou", "stateless"]
  },
  {
    id: "official-method",
    title: "Object: Method",
    url: officialSourceUrls.method,
    tags: ["method", "oop", "function-block", "interface"]
  },
  {
    id: "official-property",
    title: "Object: Property",
    url: officialSourceUrls.property,
    tags: ["property", "oop", "getter", "setter", "function-block"]
  },
  {
    id: "official-dut",
    title: "Object: DUT",
    url: officialSourceUrls.dut,
    tags: ["dut", "data-type", "structure", "enum", "union"]
  },
  {
    id: "official-enum",
    title: "Enumeration Data Types",
    url: officialSourceUrls.enum,
    tags: ["enum", "data-type", "state-machine", "structured-text"]
  },
  {
    id: "official-gvl",
    title: "Object: Global Variable List",
    url: officialSourceUrls.gvl,
    tags: ["gvl", "global-variable", "variable", "namespace"]
  },
  {
    id: "official-gvl-namespaces",
    title: "Global Variable List Namespaces",
    url: officialSourceUrls.gvlNamespaces,
    tags: ["gvl", "namespace", "global-variable", "library"]
  },
  {
    id: "official-persistent-variables",
    title: "Persistent Variables",
    url: officialSourceUrls.persistentVariables,
    tags: ["persistent-variable", "retain", "data", "runtime"]
  },
  {
    id: "official-operators",
    title: "Operators",
    url: officialSourceUrls.operators,
    tags: ["operator", "expression", "structured-text", "syntax"]
  },
  {
    id: "official-st-fb-call",
    title: "ST Function Block Call",
    url: officialSourceUrls.functionBlockCall,
    tags: ["structured-text", "function-block", "call", "timer"]
  },
  {
    id: "official-oop",
    title: "Object-Oriented Programming",
    url: officialSourceUrls.objectOriented,
    tags: ["oop", "methods", "interfaces", "properties", "inheritance"]
  },
  {
    id: "official-time-constants",
    title: "TIME and LTIME Constants",
    url: officialSourceUrls.timeConstants,
    tags: ["time", "timer", "constants", "structured-text"]
  },
  {
    id: "official-ton",
    title: "TON Timer Function Block",
    url: officialSourceUrls.ton,
    tags: ["timer", "ton", "standard-library", "function-block"]
  },
  {
    id: "official-tof",
    title: "TOF Timer Function Block",
    url: officialSourceUrls.tof,
    tags: ["timer", "tof", "standard-library", "function-block"]
  },
  {
    id: "official-tp",
    title: "TP Timer Function Block",
    url: officialSourceUrls.tp,
    tags: ["timer", "tp", "standard-library", "function-block"]
  },
  {
    id: "official-standard-library",
    title: "Standard Library Documentation",
    url: officialSourceUrls.standardLibrary,
    tags: ["standard-library", "timer", "counter", "trigger", "string"]
  },
  {
    id: "official-ctu",
    title: "CTU Counter Function Block",
    url: officialSourceUrls.ctu,
    tags: ["counter", "ctu", "standard-library", "function-block"]
  },
  {
    id: "official-ctd",
    title: "CTD Counter Function Block",
    url: officialSourceUrls.ctd,
    tags: ["counter", "ctd", "standard-library", "function-block"]
  },
  {
    id: "official-r-trig",
    title: "R_TRIG Trigger Function Block",
    url: officialSourceUrls.rTrig,
    tags: ["trigger", "edge", "r_trig", "standard-library", "function-block"]
  },
  {
    id: "official-f-trig",
    title: "F_TRIG Trigger Function Block",
    url: officialSourceUrls.fTrig,
    tags: ["trigger", "edge", "f_trig", "standard-library", "function-block"]
  },
  {
    id: "official-visualization",
    title: "CODESYS Visualization",
    url: officialSourceUrls.visualization,
    tags: ["visualization", "hmi", "ui", "webvisu"]
  },
  {
    id: "official-fieldbus",
    title: "CODESYS Fieldbus",
    url: officialSourceUrls.fieldbus,
    tags: ["fieldbus", "ethercat", "profinet", "modbus", "canopen"]
  },
  {
    id: "official-softmotion",
    title: "CODESYS SoftMotion",
    url: officialSourceUrls.softmotion,
    tags: ["softmotion", "motion", "axis", "cnc", "robotics"]
  },
  {
    id: "official-task-monitoring",
    title: "Task Configuration Monitoring",
    url: officialSourceUrls.taskMonitoring,
    tags: ["task", "monitoring", "debugging", "runtime"]
  },
  {
    id: "official-task-configuration",
    title: "Task Configuration",
    url: officialSourceUrls.taskConfiguration,
    tags: ["task", "cycle", "watchdog", "priority", "runtime"]
  },
  {
    id: "official-multicore",
    title: "Multicore And Task-Local Variables",
    url: officialSourceUrls.multicore,
    tags: ["multicore", "task", "task-local", "runtime", "performance"]
  }
];
