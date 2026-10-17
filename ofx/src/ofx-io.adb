with Ada.Command_Line;
with GNAT.OS_Lib;

package body Ofx.IO with
  SPARK_Mode => Off
is

   procedure Get_Mode (Result : out Mode) is
   begin
      Result := Usage_Mode;
      if Ada.Command_Line.Argument_Count = 1 then
         declare
            Argument : constant String := Ada.Command_Line.Argument (1);
         begin
            if Argument = "parse" then
               Result := Parse_Mode;
            elsif Argument = "request" then
               Result := Request_Mode;
            elsif Argument = "--version" then
               Result := Version_Mode;
            end if;
         end;
      end if;
   end Get_Mode;

   procedure Read_Standard_Input (Buffer : in out String; Length : out Natural; Ok : out Boolean) is
      Count : Integer;
   begin
      Length := 0;
      Ok := True;
      while Length < Buffer'Last loop
         Count := GNAT.OS_Lib.Read
           (GNAT.OS_Lib.Standin, Buffer (Length + 1)'Address, Buffer'Last - Length);
         if Count < 0 then
            Ok := False;
            return;
         end if;
         exit when Count = 0;
         Length := Length + Count;
      end loop;
   end Read_Standard_Input;

   procedure Write (Target : Stream; Data : String; Ok : out Boolean) is
      Descriptor : constant GNAT.OS_Lib.File_Descriptor :=
        (if Target = Standard_Output then GNAT.OS_Lib.Standout else GNAT.OS_Lib.Standerr);
      Done  : Natural := 0;
      Count : Integer;
   begin
      Ok := True;
      while Done < Data'Length loop
         Count := GNAT.OS_Lib.Write
           (Descriptor, Data (Data'First + Done)'Address, Data'Length - Done);
         if Count <= 0 then
            Ok := False;
            return;
         end if;
         Done := Done + Count;
      end loop;
   end Write;

   procedure Set_Exit_Status (Code : Natural) is
   begin
      Ada.Command_Line.Set_Exit_Status (Ada.Command_Line.Exit_Status (Code));
   end Set_Exit_Status;

end Ofx.IO;
