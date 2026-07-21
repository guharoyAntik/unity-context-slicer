using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace CSharpScanner
{
    public class ScannerCore
    {
        private Compilation? _compilation;
        private readonly Dictionary<string, ExternalClass> _classesByFullName = new();
        private readonly Dictionary<string, string> _nodeIdByMember = new();
        private readonly List<CallRelation> _callRelations = new();
        private readonly List<StructureRelation> _structureRelations = new();
        private int _nodeIdCounter = 0;

        private string? _currentAnalyzingMethodId = null;
        private readonly Stack<string> _methodAnalysisStack = new();

        private readonly HashSet<string> _unityNamespaceBlacklist = new()
        {
            "UnityEngine.Debug",
            "UnityEngine.Transform",
            "UnityEngine.Time",
            "UnityEngine.Input",
            "UnityEngine.Physics",
            "UnityEngine.Mathf",
            "UnityEngine.Application",
            "UnityEngine.Screen",
            "UnityEngine.Camera",
            "UnityEngine.Light",
            "UnityEngine.Renderer",
            "UnityEngine.Collider",
        };

        private readonly HashSet<string> _structuralUnityMethods = new()
        {
            "UnityEngine.Object.Instantiate",
            "UnityEngine.Object.Destroy",
            "UnityEngine.SceneManagement.SceneManager.LoadScene",
            "UnityEngine.Resources.Load",
            "UnityEngine.GameObject.Find",
        };

        public async Task<ScannerOutput> ScanProjectAsync(string projectPath)
        {
            await LoadSourceFiles(projectPath);
            await ScanCodeStructure();

            return new ScannerOutput
            {
                ExternalClasses = _classesByFullName.Values.ToList(),
                Calls = _callRelations,
                StructureRelations = _structureRelations
            };
        }

        private async Task LoadSourceFiles(string projectPath)
        {
            var csFiles = Directory
                .GetFiles(projectPath, "*.cs", SearchOption.AllDirectories)
                .Where(f => !f.Replace('\\', '/').Contains("/Editor/") && !f.Replace('\\', '/').Contains("/Packages/") && !f.Replace('\\', '/').Contains("/Library/") && !f.Replace('\\', '/').Contains("/Temp/"))
                .ToList();

            var syntaxTrees = new List<SyntaxTree>();
            foreach (var file in csFiles)
            {
                try
                {
                    var sourceCode = await File.ReadAllTextAsync(file);
                    var tree = CSharpSyntaxTree.ParseText(sourceCode, path: file);
                    syntaxTrees.Add(tree);
                }
                catch (Exception)
                {
                    // Ignore unreadable files
                }
            }

            var references = new List<MetadataReference>();
            // Add basic references
            var trustedAssembliesPaths = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator);
            foreach (var refPath in trustedAssembliesPaths)
            {
                if (refPath.EndsWith(".dll"))
                    references.Add(MetadataReference.CreateFromFile(refPath));
            }

            _compilation = CSharpCompilation.Create(
                "UnityProject",
                syntaxTrees,
                references,
                new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary)
            );
        }

        private async Task ScanCodeStructure()
        {
            if (_compilation == null) return;

            foreach (var syntaxTree in _compilation.SyntaxTrees)
            {
                var root = await syntaxTree.GetRootAsync();
                AnalyzeSourceFile(root, _compilation);
            }
        }

        private void AnalyzeSourceFile(SyntaxNode root, Compilation compilation)
        {
            var semanticModel = compilation.GetSemanticModel(root.SyntaxTree);
            var typeDeclarations = root.DescendantNodes()
                .Where(n => n is ClassDeclarationSyntax || n is InterfaceDeclarationSyntax || n is StructDeclarationSyntax || n is EnumDeclarationSyntax);

            foreach (var decl in typeDeclarations)
            {
                switch (decl)
                {
                    case ClassDeclarationSyntax cls:
                        var classInfo = AnalyzeClass(cls, semanticModel);
                        if (classInfo != null && (classInfo.Methods.Count > 0 || classInfo.IsMonoBehaviour))
                        {
                            _classesByFullName[classInfo.Fqn] = classInfo;
                        }
                        break;
                    case InterfaceDeclarationSyntax iface:
                        AnalyzeInterface(iface, semanticModel);
                        break;
                    case StructDeclarationSyntax st:
                        AnalyzeStruct(st, semanticModel);
                        break;
                    case EnumDeclarationSyntax en:
                        AnalyzeEnum(en, semanticModel);
                        break;
                }
            }

            AnalyzeCallRelations(root, semanticModel);
        }

        private void AnalyzeCallRelations(SyntaxNode root, SemanticModel semanticModel)
        {
            var methodDeclarations = root.DescendantNodes().OfType<MethodDeclarationSyntax>();
            foreach (var methodDecl in methodDeclarations)
            {
                var methodSymbol = semanticModel.GetDeclaredSymbol(methodDecl);
                if (methodSymbol == null) continue;

                string methodFullName = NormalizeFullName($"{methodSymbol.ContainingType.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat)}.{methodSymbol.Name}");
                if (!_nodeIdByMember.TryGetValue(methodFullName, out string? currentMethodId)) continue;

                if (_currentAnalyzingMethodId != null) _methodAnalysisStack.Push(_currentAnalyzingMethodId);
                _currentAnalyzingMethodId = currentMethodId;

                try
                {
                    var invocations = methodDecl.DescendantNodes().OfType<InvocationExpressionSyntax>();
                    foreach (var invocation in invocations) AnalyzeMethodInvocation(invocation, semanticModel);

                    var memberAccesses = methodDecl.DescendantNodes().OfType<MemberAccessExpressionSyntax>();
                    foreach (var memberAccess in memberAccesses) AnalyzeMemberAccess(memberAccess, semanticModel);

                    var objectCreations = methodDecl.DescendantNodes().OfType<ObjectCreationExpressionSyntax>();
                    foreach (var objectCreation in objectCreations) AnalyzeObjectCreation(objectCreation, semanticModel);
                }
                finally
                {
                    _currentAnalyzingMethodId = _methodAnalysisStack.Count > 0 ? _methodAnalysisStack.Pop() : null;
                }
            }
        }

        private void AnalyzeMethodInvocation(InvocationExpressionSyntax invocation, SemanticModel semanticModel)
        {
            var symbolInfo = semanticModel.GetSymbolInfo(invocation);
            if (symbolInfo.Symbol is not IMethodSymbol method || _currentAnalyzingMethodId == null) return;

            string targetAssembly = method.ContainingAssembly?.Name ?? "UnknownAssembly";
            string targetFullName = NormalizeFullName($"{method.ContainingType.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat)}.{method.Name}");

            if (IsUnityApiFiltered(targetFullName, targetFullName)) return;

            _callRelations.Add(new CallRelation
            {
                FromNodeId = _currentAnalyzingMethodId,
                ToNodeId = targetFullName,
                CallType = "method",
                MethodName = method.Name,
                LibraryName = targetAssembly
            });
        }

        private bool IsUnityApiFiltered(string targetNamespace, string targetMethodFullName)
        {
            if (_structuralUnityMethods.Contains(targetMethodFullName)) return false;
            foreach (var ns in _unityNamespaceBlacklist)
            {
                if (targetNamespace.StartsWith(ns)) return true;
            }
            return false;
        }

        private void AnalyzeMemberAccess(MemberAccessExpressionSyntax memberAccess, SemanticModel semanticModel)
        {
            if (_currentAnalyzingMethodId == null) return;
            var symbolInfo = semanticModel.GetSymbolInfo(memberAccess);
            if (symbolInfo.Symbol is not IFieldSymbol field) return;
            if (field.ContainingType.TypeKind == TypeKind.Enum)
            {
                string enumTypeNamespace = field.ContainingType.ContainingNamespace?.ToDisplayString() ?? "";
                if (enumTypeNamespace.StartsWith("UnityEngine") || enumTypeNamespace.StartsWith("System")) return;

                string enumNodeId = NormalizeFullName($"{enumTypeNamespace}.{field.ContainingType.Name}");
                if (!_structureRelations.Any(r => r.FromNodeId == _currentAnalyzingMethodId && r.ToNodeId == enumNodeId && r.RelationType == "uses"))
                {
                    _structureRelations.Add(new StructureRelation { FromNodeId = _currentAnalyzingMethodId, ToNodeId = enumNodeId, RelationType = "uses" });
                }
            }
        }

        private void AnalyzeObjectCreation(ObjectCreationExpressionSyntax objectCreation, SemanticModel semanticModel)
        {
            if (_currentAnalyzingMethodId == null) return;
            var symbolInfo = semanticModel.GetSymbolInfo(objectCreation);
            if (symbolInfo.Symbol is not INamedTypeSymbol typeSymbol || typeSymbol.TypeKind != TypeKind.Struct) return;

            string structTypeNamespace = typeSymbol.ContainingNamespace?.ToDisplayString() ?? "";
            if (structTypeNamespace.StartsWith("UnityEngine") || structTypeNamespace.StartsWith("System")) return;

            string structNodeId = NormalizeFullName($"{structTypeNamespace}.{typeSymbol.Name}");
            if (!_structureRelations.Any(r => r.FromNodeId == _currentAnalyzingMethodId && r.ToNodeId == structNodeId && r.RelationType == "uses"))
            {
                _structureRelations.Add(new StructureRelation { FromNodeId = _currentAnalyzingMethodId, ToNodeId = structNodeId, RelationType = "uses" });
            }
        }

        private ExternalClass? AnalyzeClass(ClassDeclarationSyntax classDecl, SemanticModel semanticModel)
        {
            var classSymbol = semanticModel.GetDeclaredSymbol(classDecl);
            if (classSymbol == null) return null;

            string className = classSymbol.Name;
            string namespaceName = classSymbol.ContainingNamespace?.ToDisplayString() ?? "";
            string nodeId = NormalizeFullName(string.IsNullOrEmpty(namespaceName) ? className : $"{namespaceName}.{className}");

            var classInfo = new ExternalClass
            {
                ClassName = className,
                NamespaceName = namespaceName,
                Type = "class",
                IsMonoBehaviour = IsSubclassOf(classSymbol, "UnityEngine.MonoBehaviour")
            };
            _nodeIdByMember[nodeId] = GenerateNodeId();

            if (classSymbol.BaseType != null && classSymbol.BaseType.Name != "Object")
            {
                _structureRelations.Add(new StructureRelation { FromNodeId = nodeId, ToNodeId = NormalizeFullName(classSymbol.BaseType.ToDisplayString()), RelationType = "inherits" });
            }

            foreach (var iface in classSymbol.AllInterfaces)
            {
                _structureRelations.Add(new StructureRelation { FromNodeId = nodeId, ToNodeId = NormalizeFullName(iface.ToDisplayString()), RelationType = "implements" });
            }

            foreach (var field in classSymbol.GetMembers().OfType<IFieldSymbol>())
            {
                var fieldType = field.Type;
                if (fieldType.TypeKind is TypeKind.Struct or TypeKind.Enum)
                {
                    string typeNamespace = fieldType.ContainingNamespace?.ToDisplayString() ?? "";
                    if (typeNamespace.StartsWith("UnityEngine") || typeNamespace.StartsWith("System")) continue;

                    string usedType = NormalizeFullName(string.IsNullOrEmpty(typeNamespace) ? fieldType.Name : $"{typeNamespace}.{fieldType.Name}");
                    if (!_structureRelations.Any(r => r.FromNodeId == nodeId && r.ToNodeId == usedType && r.RelationType == "uses"))
                    {
                        _structureRelations.Add(new StructureRelation { FromNodeId = nodeId, ToNodeId = usedType, RelationType = "uses" });
                    }
                }
            }

            foreach (var member in classDecl.Members.OfType<MethodDeclarationSyntax>())
            {
                var methodSymbol = semanticModel.GetDeclaredSymbol(member);
                if (methodSymbol == null) continue;

                string methodFullName = NormalizeFullName($"{classSymbol.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat)}.{methodSymbol.Name}");
                string methodId = GenerateNodeId();
                _nodeIdByMember[methodFullName] = methodId;

                classInfo.Methods.Add(new MethodStructure
                {
                    MethodName = methodSymbol.Name,
                    MethodType = DetermineMethodType(methodSymbol),
                    IsStatic = methodSymbol.IsStatic,
                    NodeId = methodId
                });
            }

            foreach (var member in classDecl.Members.OfType<EventFieldDeclarationSyntax>())
            {
                foreach (var variable in member.Declaration.Variables)
                {
                    var eventSymbol = semanticModel.GetDeclaredSymbol(variable);
                    if (eventSymbol != null)
                    {
                        classInfo.Events.Add(new EventStructure
                        {
                            EventName = eventSymbol.Name,
                            NodeId = GenerateNodeId(),
                            IsStatic = eventSymbol.IsStatic
                        });
                    }
                }
            }

            return classInfo;
        }

        private void AnalyzeInterface(InterfaceDeclarationSyntax ifaceDecl, SemanticModel semanticModel)
        {
            var symbol = semanticModel.GetDeclaredSymbol(ifaceDecl);
            if (symbol == null) return;
            string nodeId = NormalizeFullName(string.IsNullOrEmpty(symbol.ContainingNamespace?.ToDisplayString()) ? symbol.Name : $"{symbol.ContainingNamespace.ToDisplayString()}.{symbol.Name}");
            _classesByFullName[nodeId] = new ExternalClass { ClassName = symbol.Name, NamespaceName = symbol.ContainingNamespace?.ToDisplayString() ?? "", Type = "interface" };
        }

        private void AnalyzeStruct(StructDeclarationSyntax structDecl, SemanticModel semanticModel)
        {
            var symbol = semanticModel.GetDeclaredSymbol(structDecl);
            if (symbol == null) return;
            string nodeId = NormalizeFullName(string.IsNullOrEmpty(symbol.ContainingNamespace?.ToDisplayString()) ? symbol.Name : $"{symbol.ContainingNamespace.ToDisplayString()}.{symbol.Name}");
            _classesByFullName[nodeId] = new ExternalClass { ClassName = symbol.Name, NamespaceName = symbol.ContainingNamespace?.ToDisplayString() ?? "", Type = "struct" };
        }

        private void AnalyzeEnum(EnumDeclarationSyntax enumDecl, SemanticModel semanticModel)
        {
            var symbol = semanticModel.GetDeclaredSymbol(enumDecl);
            if (symbol == null) return;
            string nodeId = NormalizeFullName(string.IsNullOrEmpty(symbol.ContainingNamespace?.ToDisplayString()) ? symbol.Name : $"{symbol.ContainingNamespace.ToDisplayString()}.{symbol.Name}");
            _classesByFullName[nodeId] = new ExternalClass { ClassName = symbol.Name, NamespaceName = symbol.ContainingNamespace?.ToDisplayString() ?? "", Type = "enum" };
        }

        private string DetermineMethodType(IMethodSymbol methodSymbol)
        {
            string name = methodSymbol.Name;
            if (new[] { "Start", "Awake", "Update", "FixedUpdate", "LateUpdate", "OnEnable", "OnDisable", "OnDestroy" }.Contains(name)) return "unity_lifecycle";
            if (name.StartsWith("On") && name.Contains("Trigger")) return "unity_callback";
            if (methodSymbol.IsImplicitlyDeclared || methodSymbol.MethodKind == MethodKind.PropertyGet || methodSymbol.MethodKind == MethodKind.PropertySet) return "generated";
            return "custom";
        }

        private string GenerateNodeId() => $"node_{++_nodeIdCounter}";

        private static string NormalizeFullName(string name) => name.StartsWith("global::", StringComparison.Ordinal) ? name.Substring(8) : name;

        private bool IsSubclassOf(INamedTypeSymbol? type, string targetBase)
        {
            while (type != null)
            {
                if (type.ToDisplayString() == targetBase) return true;
                type = type.BaseType;
            }
            return false;
        }
    }
}
