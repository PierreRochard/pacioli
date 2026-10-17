--  pacioli-ofx parse    OFX on standard input; JSON on standard output.
--  pacioli-ofx request  key=value lines on standard input; an OFX request.
--  pacioli-ofx --version
--
--  Exit status: 0 success, 1 unreadable input or unwritable output,
--  2 rejected input (a JSON error on standard error), 64 usage.

with Ofx;
with Ofx.Emit;
with Ofx.IO;
with Ofx.Json;
with Ofx.Parser;
with Ofx.Requests;
with Ofx.Storage;

procedure Pacioli_Ofx_Main with SPARK_Mode is

   use type Ofx.IO.Mode;
   use type Ofx.Parser.Error_Kind;

   Version : constant String := "pacioli-ofx 1.0.0";

   Mode   : Ofx.IO.Mode;
   Length : Natural;
   Ok     : Boolean;
   Output : Ofx.Json.Writer;
   Errors : Ofx.Json.Writer;
   Status : Natural := 0;

   procedure Reject (Message : String) is
   begin
      Ofx.Json.Put (Errors, Message);
      Ofx.Json.Put (Errors, ASCII.LF);
      Status := 2;
   end Reject;

begin
   Errors.Target := Ofx.IO.Standard_Error;
   Ofx.IO.Get_Mode (Mode);

   case Mode is
      when Ofx.IO.Usage_Mode =>
         Ofx.Json.Put (Errors, "usage: pacioli-ofx parse|request|--version");
         Ofx.Json.Put (Errors, ASCII.LF);
         Status := 64;

      when Ofx.IO.Version_Mode =>
         Ofx.Json.Put (Output, Version);
         Ofx.Json.Put (Output, ASCII.LF);

      when Ofx.IO.Parse_Mode | Ofx.IO.Request_Mode =>
         Ofx.IO.Read_Standard_Input (Ofx.Storage.Input, Length, Ok);
         if not Ok then
            Ofx.Json.Put (Errors, "{""error"":""input_unreadable""}");
            Ofx.Json.Put (Errors, ASCII.LF);
            Status := 1;
         elsif Length > Ofx.Max_Input then
            Reject ("{""error"":""input_too_large""}");
         elsif Mode = Ofx.IO.Parse_Mode then
            declare
               Error : Ofx.Parser.Error_Record;
            begin
               Ofx.Parser.Parse (Ofx.Storage.Input (1 .. Length), Ofx.Storage.Doc, Error);
               if Error.Kind = Ofx.Parser.No_Error then
                  Ofx.Emit.Document (Output, Ofx.Storage.Input (1 .. Length), Ofx.Storage.Doc);
               else
                  Ofx.Emit.Error (Errors, Ofx.Storage.Input (1 .. Length), Error);
                  Status := 2;
               end if;
            end;
         else
            declare
               Values  : Ofx.Requests.Value_Array;
               Bad     : Ofx.Requests.Key;
               Has_Bad : Boolean;
            begin
               Ofx.Requests.Parse (Ofx.Storage.Input (1 .. Length), Values, Ok, Bad, Has_Bad);
               if Ok then
                  Ofx.Requests.Write (Output, Ofx.Storage.Input (1 .. Length), Values);
               elsif Has_Bad then
                  Reject ("{""error"":""request_invalid"",""field"":""" & Ofx.Requests.Name (Bad)
                          & """}");
               else
                  Reject ("{""error"":""request_invalid""}");
               end if;
            end;
         end if;
   end case;

   Ofx.Json.Flush (Output);
   Ofx.Json.Flush (Errors);
   if (Output.Failed or else Errors.Failed) and then Status = 0 then
      Status := 1;
   end if;
   Ofx.IO.Set_Exit_Status (Status);
end Pacioli_Ofx_Main;
