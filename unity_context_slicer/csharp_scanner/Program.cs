using System;
using System.Text.Json;
using System.Threading.Tasks;

namespace CSharpScanner
{
    class Program
    {
        static async Task Main(string[] args)
        {
            if (args.Length == 0)
            {
                Console.Error.WriteLine("Usage: CSharpScanner <project_path>");
                Environment.Exit(1);
            }

            string projectPath = args[0];
            try
            {
                var scanner = new ScannerCore();
                var output = await scanner.ScanProjectAsync(projectPath);

                var options = new JsonSerializerOptions
                {
                    WriteIndented = true,
                    PropertyNamingPolicy = JsonNamingPolicy.CamelCase
                };

                string json = JsonSerializer.Serialize(output, options);
                Console.WriteLine(json);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Error scanning project: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                Environment.Exit(1);
            }
        }
    }
}
