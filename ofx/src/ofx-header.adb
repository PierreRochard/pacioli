with Ofx.Chars; use Ofx.Chars;

package body Ofx.Header with SPARK_Mode is

   use Ofx.Model;

   Max_Value : constant := 36;

   function Is_Value (C : Character) return Boolean is
     (Is_Alphanumeric (C) or else C in '-' | '_' | '.');

   function Starts_With (Text : String; Position : Positive; Literal : String) return Boolean is
     (Literal'Length <= Text'Last - Position + 1
      and then Text (Position .. Position + Literal'Length - 1) = Literal)
   with Pre => Text'First = 1 and then Text'Last <= Max_Input
               and then Position <= Text'Last + 1 and then Literal'Length >= 1;

   function Same_Ignoring_Case (Text : String; S : Span; Literal : String) return Boolean is
     (Length (S) = Literal'Length
      and then (for all I in 0 .. Literal'Length - 1 =>
                  (if Text (S.First + I) in 'a' .. 'z'
                   then Character'Val (Character'Pos (Text (S.First + I)) - 32)
                   else Text (S.First + I)) = Literal (Literal'First + I)))
   with Pre => Within (S, Text);

   procedure Skip_Space (Text : String; Position : in out Positive)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Position <= Text'Last + 1,
     Post => Position in Position'Old .. Text'Last + 1
   is
   begin
      while Position <= Text'Last and then Is_Space (Text (Position)) loop
         pragma Loop_Invariant (Position in Position'Loop_Entry .. Text'Last);
         pragma Loop_Variant (Increases => Position);
         Position := Position + 1;
      end loop;
   end Skip_Space;

   --  A run of 1 .. Max_Value value characters, ending before Stop.
   procedure Value
     (Text     : String;
      Position : in out Positive;
      Result   : out Span;
      Ok       : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Position <= Text'Last + 1,
     Post => Position in Position'Old .. Text'Last + 1 and then Within (Result, Text)
             and then (if Ok then not Is_Empty (Result))
   is
      First : constant Positive := Position;
   begin
      Result := No_Span;
      Ok := False;
      while Position <= Text'Last and then Is_Value (Text (Position)) loop
         pragma Loop_Invariant (Position in First .. Text'Last);
         pragma Loop_Invariant (Position - First < Max_Value);
         pragma Loop_Variant (Increases => Position);
         if Position - First + 1 = Max_Value then
            return;
         end if;
         Position := Position + 1;
      end loop;
      if Position > First then
         Result := (First => First, Last => Position - 1);
         Ok := True;
      end if;
   end Value;

   function Three_Digits (Text : String; S : Span) return Boolean is
     (Length (S) = 3 and then Is_Digit (Text (S.First)) and then Is_Digit (Text (S.First + 1))
      and then Is_Digit (Text (S.First + 2)))
   with Pre => Within (S, Text);

   function Number (Text : String; S : Span) return Natural is
     (Digit (Text (S.First)) * 100 + Digit (Text (S.First + 1)) * 10 + Digit (Text (S.First + 2)))
   with Pre => Within (S, Text) and then Three_Digits (Text, S),
        Post => Number'Result <= 999;

   function Field_Name (Index : Positive) return String is
     (case Index is
         when 1 => "OFXHEADER:", when 2 => "DATA:", when 3 => "VERSION:",
         when 4 => "SECURITY:", when 5 => "ENCODING:", when 6 => "CHARSET:",
         when 7 => "COMPRESSION:", when 8 => "OLDFILEUID:", when others => "NEWFILEUID:")
   with Post => Field_Name'Result'Length >= 1;

   procedure Version_1
     (Text     : String;
      Position : Positive;
      Result   : in out Header_Record;
      Ok       : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Position <= Text'Last + 1,
     Post => (if Ok then Well_Formed (Text, Result))
   is
      type Value_Array is array (1 .. 9) of Span;
      Values  : Value_Array := [others => No_Span];
      Current : Positive := Position;
      Found   : Boolean;
   begin
      Ok := False;
      for Index in Values'Range loop
         pragma Loop_Invariant (Current <= Text'Last + 1);
         pragma Loop_Invariant (for all I in Values'Range => Within (Values (I), Text));
         if Index > 1 then
            if Current > Text'Last or else not Is_Space (Text (Current)) then
               return;
            end if;
            Skip_Space (Text, Current);
         end if;
         if not Starts_With (Text, Current, Field_Name (Index)) then
            return;
         end if;
         Current := Current + Field_Name (Index)'Length;
         Value (Text, Current, Values (Index), Found);
         if not Found then
            return;
         end if;
      end loop;
      --  White space separates the header from <OFX>.
      if Current > Text'Last or else not Is_Space (Text (Current)) then
         return;
      end if;

      if not Matches (Text, Values (1), "100") or else not Matches (Text, Values (2), "OFXSGML")
        or else not Three_Digits (Text, Values (3))
        or else not (Matches (Text, Values (4), "NONE") or else Matches (Text, Values (4), "TYPE1"))
        or else not Matches (Text, Values (7), "NONE")
      then
         return;
      end if;
      Result.Version := Number (Text, Values (3));
      if Result.Version not in 102 | 103 | 151 | 160 then
         return;
      end if;

      if Matches (Text, Values (5), "UTF-8") then
         Result.Decoding := Utf_8;
      elsif not Matches (Text, Values (5), "USASCII") then
         return;
      elsif Matches (Text, Values (6), "1252") then
         Result.Decoding := Windows_1252;
      elsif Matches (Text, Values (6), "ISO-8859-1") then
         Result.Decoding := Latin_1;
      elsif Matches (Text, Values (6), "NONE") then
         Result.Decoding := Us_Ascii;
      else
         return;
      end if;
      if Result.Decoding = Utf_8
        and then not (Matches (Text, Values (6), "1252")
                      or else Matches (Text, Values (6), "ISO-8859-1")
                      or else Matches (Text, Values (6), "NONE"))
      then
         return;
      end if;

      Result.Major := 1;
      Result.Security := Values (4);
      Result.Encoding := Values (5);
      Result.Charset := Values (6);
      Result.Compression := Values (7);
      Result.Old_File := Values (8);
      Result.New_File := Values (9);
      Result.Body_Start := Current;
      Ok := True;
   end Version_1;

   --  name="value" or name='value'.
   procedure Attribute
     (Text     : String;
      Position : in out Positive;
      Name     : out Span;
      Result   : out Span;
      Ok       : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Position <= Text'Last + 1,
     Post => Position in Position'Old .. Text'Last + 1
             and then Within (Name, Text) and then Within (Result, Text)
   is
      First : constant Positive := Position;
      Quote : Character;
   begin
      Name := No_Span;
      Result := No_Span;
      Ok := False;
      while Position <= Text'Last and then Text (Position) in 'a' .. 'z' | 'A' .. 'Z' loop
         pragma Loop_Invariant (Position in First .. Text'Last);
         pragma Loop_Invariant (Position - First < 16);
         pragma Loop_Variant (Increases => Position);
         if Position - First + 1 = 16 then
            return;
         end if;
         Position := Position + 1;
      end loop;
      if Position = First or else Position > Text'Last - 1 or else Text (Position) /= '='
        or else Text (Position + 1) not in '"' | '''
      then
         return;
      end if;
      Name := (First => First, Last => Position - 1);
      Quote := Text (Position + 1);
      Position := Position + 2;
      Value (Text, Position, Result, Ok);
      if not Ok or else Position > Text'Last or else Text (Position) /= Quote then
         Ok := False;
         return;
      end if;
      Position := Position + 1;
   end Attribute;

   type Attribute_Names is array (1 .. 5) of Span;

   --  The attributes of a processing instruction up to and past "?>". Names
   --  must be among Allowed and occur once each.
   procedure Instruction
     (Text     : String;
      Position : in out Positive;
      Kind     : Positive;
      Values   : out Attribute_Names;
      Ok       : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Position <= Text'Last + 1
             and then Kind in 1 .. 2,
     Post => Position in Position'Old .. Text'Last + 1
             and then (for all I in Values'Range => Within (Values (I), Text))
   is
      Name   : Span;
      Result : Span;
      Found  : Boolean;
      Slot   : Natural;
   begin
      Values := [others => No_Span];
      Ok := False;
      loop
         pragma Loop_Invariant (Position in Position'Loop_Entry .. Text'Last + 1);
         pragma Loop_Invariant (for all I in Values'Range => Within (Values (I), Text));
         pragma Loop_Variant (Increases => Position);
         Skip_Space (Text, Position);
         if Starts_With (Text, Position, "?>") then
            Position := Position + 2;
            Ok := True;
            return;
         end if;
         declare
            Before : constant Positive := Position;
         begin
            Attribute (Text, Position, Name, Result, Found);
            if not Found or else Position = Before then
               return;
            end if;
         end;
         --  1: <?xml version encoding standalone?>; 2: <?OFX ...?>
         if Kind = 1 then
            Slot := (if Matches (Text, Name, "version") then 1
                     elsif Matches (Text, Name, "encoding") then 2
                     elsif Matches (Text, Name, "standalone") then 3 else 0);
         else
            Slot := (if Matches (Text, Name, "OFXHEADER") then 1
                     elsif Matches (Text, Name, "VERSION") then 2
                     elsif Matches (Text, Name, "SECURITY") then 3
                     elsif Matches (Text, Name, "OLDFILEUID") then 4
                     elsif Matches (Text, Name, "NEWFILEUID") then 5 else 0);
         end if;
         if Slot = 0 or else not Is_Empty (Values (Slot)) then
            return;
         end if;
         Values (Slot) := Result;
      end loop;
   end Instruction;

   procedure Version_2
     (Text     : String;
      Position : Positive;
      Result   : in out Header_Record;
      Ok       : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Position <= Text'Last + 1,
     Post => (if Ok then Well_Formed (Text, Result))
   is
      Current : Positive := Position;
      Xml     : Attribute_Names;
      Ofx_PI  : Attribute_Names;
      Found   : Boolean;
   begin
      Ok := False;
      if not Starts_With (Text, Current, "<?xml") then
         return;
      end if;
      Current := Current + 5;
      Instruction (Text, Current, 1, Xml, Found);
      if not Found then
         return;
      end if;
      Skip_Space (Text, Current);
      if not Starts_With (Text, Current, "<?OFX") then
         return;
      end if;
      Current := Current + 5;
      Instruction (Text, Current, 2, Ofx_PI, Found);
      if not Found then
         return;
      end if;

      if not Matches (Text, Xml (1), "1.0")
        or else not (Is_Empty (Xml (3)) or else Matches (Text, Xml (3), "no")
                     or else Matches (Text, Xml (3), "yes"))
        or else not Matches (Text, Ofx_PI (1), "200")
        or else not Three_Digits (Text, Ofx_PI (2))
        or else not (Matches (Text, Ofx_PI (3), "NONE") or else Matches (Text, Ofx_PI (3), "TYPE1"))
        or else Is_Empty (Ofx_PI (4)) or else Is_Empty (Ofx_PI (5))
      then
         return;
      end if;
      Result.Version := Number (Text, Ofx_PI (2));
      if Result.Version not in 200 | 201 | 202 | 203 | 210 | 211 | 220 then
         return;
      end if;
      if Is_Empty (Xml (2)) or else Same_Ignoring_Case (Text, Xml (2), "UTF-8") then
         Result.Decoding := Utf_8;
      elsif Same_Ignoring_Case (Text, Xml (2), "US-ASCII") then
         Result.Decoding := Us_Ascii;
      else
         return;
      end if;

      Result.Major := 2;
      Result.Security := Ofx_PI (3);
      Result.Encoding := Xml (2);
      Result.Charset := No_Span;
      Result.Compression := No_Span;
      Result.Old_File := Ofx_PI (4);
      Result.New_File := Ofx_PI (5);
      Result.Body_Start := Current;
      Ok := True;
   end Version_2;

   procedure Parse (Text : String; Result : out Header_Record; Ok : out Boolean) is
      Byte_Order_Mark : constant String :=
        [Character'Val (16#EF#), Character'Val (16#BB#), Character'Val (16#BF#)];
      Position : Positive := 1;
   begin
      Result := (others => <>);
      Ok := False;
      if Text'Length = 0 then
         return;
      end if;
      if Starts_With (Text, Position, Byte_Order_Mark) then
         Position := 4;
      end if;
      Skip_Space (Text, Position);
      if Starts_With (Text, Position, "OFXHEADER:") then
         Version_1 (Text, Position, Result, Ok);
      elsif Starts_With (Text, Position, "<?xml") then
         Version_2 (Text, Position, Result, Ok);
      end if;
   end Parse;

end Ofx.Header;
