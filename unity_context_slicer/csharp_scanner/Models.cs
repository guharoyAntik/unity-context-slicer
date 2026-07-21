using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace CSharpScanner
{
    public class ScannerOutput
    {
        [JsonPropertyName("externalClasses")]
        public List<ExternalClass> ExternalClasses { get; set; } = new();

        [JsonPropertyName("calls")]
        public List<CallRelation> Calls { get; set; } = new();

        [JsonPropertyName("structureRelations")]
        public List<StructureRelation> StructureRelations { get; set; } = new();
    }

    public class ExternalClass
    {
        [JsonPropertyName("className")]
        public string ClassName { get; set; } = "";

        [JsonPropertyName("namespaceName")]
        public string NamespaceName { get; set; } = "";

        [JsonPropertyName("type")]
        public string Type { get; set; } = "class"; // "class", "interface", "struct", "enum"

        [JsonPropertyName("isMonoBehaviour")]
        public bool IsMonoBehaviour { get; set; } = false;

        [JsonPropertyName("methods")]
        public List<MethodStructure> Methods { get; set; } = new();

        [JsonPropertyName("events")]
        public List<EventStructure> Events { get; set; } = new();

        [JsonPropertyName("staticInitializers")]
        public List<MethodStructure> StaticInitializers { get; set; } = new();

        [JsonPropertyName("fqn")]
        public string Fqn => string.IsNullOrEmpty(NamespaceName) ? ClassName : $"{NamespaceName}.{ClassName}";
    }

    public class MethodStructure
    {
        [JsonPropertyName("methodName")]
        public string MethodName { get; set; } = "";

        [JsonPropertyName("methodType")]
        public string MethodType { get; set; } = "";

        [JsonPropertyName("isStatic")]
        public bool IsStatic { get; set; } = false;

        [JsonPropertyName("nodeId")]
        public string NodeId { get; set; } = "";
    }

    public class EventStructure
    {
        [JsonPropertyName("eventName")]
        public string EventName { get; set; } = "";

        [JsonPropertyName("nodeId")]
        public string NodeId { get; set; } = "";

        [JsonPropertyName("isStatic")]
        public bool IsStatic { get; set; } = false;
    }

    public class CallRelation
    {
        [JsonPropertyName("fromNodeId")]
        public string FromNodeId { get; set; } = "";

        [JsonPropertyName("toNodeId")]
        public string ToNodeId { get; set; } = "";

        [JsonPropertyName("callType")]
        public string CallType { get; set; } = "";

        [JsonPropertyName("methodName")]
        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        public string? MethodName { get; set; }

        [JsonPropertyName("fieldName")]
        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        public string? FieldName { get; set; }

        [JsonPropertyName("libraryName")]
        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        public string? LibraryName { get; set; }
    }

    public class StructureRelation
    {
        [JsonPropertyName("fromNodeId")]
        public string FromNodeId { get; set; } = "";

        [JsonPropertyName("toNodeId")]
        public string ToNodeId { get; set; } = "";

        [JsonPropertyName("relationType")]
        public string RelationType { get; set; } = "";
    }
}
