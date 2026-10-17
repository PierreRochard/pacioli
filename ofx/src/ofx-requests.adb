with Ofx.Chars; use Ofx.Chars;
with Ofx.Dates;
with Ofx.Text;

package body Ofx.Requests with SPARK_Mode is

   use Ofx.Json;

   function Name (K : Key) return String is
     (case K is
         when Account    => "account",
         when Dtclient   => "dtclient",
         when Userid     => "userid",
         when Userpass   => "userpass",
         when Org        => "org",
         when Fid        => "fid",
         when Appid      => "appid",
         when Appver     => "appver",
         when Clientuid  => "clientuid",
         when Trnuid     => "trnuid",
         when Newfileuid => "newfileuid",
         when Bankid     => "bankid",
         when Acctid     => "acctid",
         when Accttype   => "accttype",
         when Dtstart    => "dtstart",
         when Dtend      => "dtend");

   function Is_Bank (Text : String; Values : Value_Array) return Boolean is
     (Matches (Text, Values (Account), "bank"))
   with Pre => Within (Values (Account), Text);

   --  Printable UTF-8 of 1 .. Max characters, without references.
   function Printable (Text : String; S : Span; Max : Positive) return Boolean
   with Pre => Text'First = 1 and then Text'Last <= Max_Input and then Within (S, Text)
   is
      Count : Natural;
      Ok    : Boolean;
   begin
      if Is_Empty (S) then
         return False;
      end if;
      Ofx.Text.Measure (Text, S, Utf_8, False, Count, Ok);
      return Ok and then Count <= Max;
   end Printable;

   --  Letters, digits and '-', as in a UUID.
   function Identifier (Text : String; S : Span) return Boolean is
     (Length (S) in 1 .. 36
      and then (for all I in S.First .. S.Last => Is_Alphanumeric (Text (I)) or else Text (I) = '-'))
   with Pre => Within (S, Text);

   function Date (Text : String; S : Span) return Boolean
   with Pre => Text'First = 1 and then Text'Last <= Max_Input and then Within (S, Text)
   is
      T  : Dates.Timestamp;
      Ok : Boolean;
   begin
      if Is_Empty (S) or else Length (S) > 14
        or else not (for all I in S.First .. S.Last => Is_Digit (Text (I)))
      then
         return False;
      end if;
      pragma Warnings (GNATprove, Off, "is set by", Reason => "Validation needs only Ok");
      Dates.Parse (Text, S, T, Ok);
      pragma Warnings (GNATprove, On, "is set by");
      return Ok;
   end Date;

   function Valid_Value (Text : String; Values : Value_Array; K : Key) return Boolean
   with
     Pre => Text'First = 1 and then Text'Last <= Max_Input
            and then (for all J in Key => Within (Values (J), Text))
   is
      S    : constant Span := Values (K);
      Bank : constant Boolean := Is_Bank (Text, Values);
   begin
      case K is
         when Account =>
            return Matches (Text, S, "bank") or else Matches (Text, S, "creditcard");
         when Dtclient =>
            return Date (Text, S);
         when Dtstart | Dtend =>
            return Is_Empty (S) or else Date (Text, S);
         when Userid =>
            return Printable (Text, S, 32);
         when Userpass =>
            return Printable (Text, S, 171);
         when Org =>
            return Is_Empty (S) or else Printable (Text, S, 32);
         when Fid =>
            return Is_Empty (S) or else (not Is_Empty (Values (Org)) and then Printable (Text, S, 32));
         when Appid =>
            return Printable (Text, S, 5);
         when Appver =>
            return Printable (Text, S, 4);
         when Clientuid =>
            return Is_Empty (S) or else Identifier (Text, S);
         when Trnuid | Newfileuid =>
            return Identifier (Text, S);
         when Bankid =>
            if Bank then
               return Length (S) = 9
                 and then (for all I in S.First .. S.Last => Is_Digit (Text (I)));
            end if;
            return Is_Empty (S);
         when Accttype =>
            if Bank then
               return Matches (Text, S, "CHECKING") or else Matches (Text, S, "SAVINGS")
                 or else Matches (Text, S, "MONEYMRKT") or else Matches (Text, S, "CREDITLINE");
            end if;
            return Is_Empty (S);
         when Acctid =>
            return Printable (Text, S, 22);
      end case;
   end Valid_Value;

   procedure Parse
     (Text    : String;
      Values  : out Value_Array;
      Ok      : out Boolean;
      Bad     : out Key;
      Has_Bad : out Boolean)
   is
      Position : Positive := 1;
      Line_End : Natural;
      Equals   : Natural;
      Found    : Boolean;
      Seen     : array (Key) of Boolean := [others => False];
   begin
      Values := [others => No_Span];
      Ok := False;
      Bad := Account;
      Has_Bad := False;

      while Position <= Text'Last loop
         pragma Loop_Invariant (Position in 1 .. Text'Last);
         pragma Loop_Invariant (for all K in Key => Within (Values (K), Text));
         pragma Loop_Variant (Increases => Position);

         Line_End := Position;
         while Line_End <= Text'Last and then Text (Line_End) /= ASCII.LF loop
            pragma Loop_Invariant (Line_End in Position .. Text'Last);
            pragma Loop_Variant (Increases => Line_End);
            Line_End := Line_End + 1;
         end loop;
         --  Text (Position .. Line_End - 1) is the line.
         Equals := 0;
         for I in Position .. Line_End - 1 loop
            if Text (I) = '=' then
               Equals := I;
               exit;
            end if;
         end loop;
         if Equals = 0 then
            return;
         end if;

         Found := False;
         for K in Key loop
            if Matches (Text, (First => Position, Last => Equals - 1), Name (K)) then
               if Seen (K) then
                  Bad := K;
                  Has_Bad := True;
                  return;
               end if;
               Seen (K) := True;
               Values (K) := (First => Equals + 1, Last => Line_End - 1);
               Found := True;
               exit;
            end if;
         end loop;
         if not Found then
            return;
         end if;
         Position := Line_End + 1;
         exit when Position > Text'Last;
      end loop;

      for K in Key loop
         pragma Loop_Invariant (for all J in Key => Within (Values (J), Text));
         if not Valid_Value (Text, Values, K) then
            Bad := K;
            Has_Bad := True;
            return;
         end if;
      end loop;
      Ok := True;
   end Parse;

   --  S as XML character data: '&', '<' and '>' escaped.
   procedure Put_Escaped (W : in out Writer; Text : String; S : Span)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Within (S, Text)
   is
   begin
      for I in S.First .. S.Last loop
         case Text (I) is
            when '&'    => Put (W, "&amp;");
            when '<'    => Put (W, "&lt;");
            when '>'    => Put (W, "&gt;");
            when others => Put (W, Text (I));
         end case;
      end loop;
   end Put_Escaped;

   procedure Element (W : in out Writer; Tag : String; Text : String; S : Span)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Within (S, Text)
   is
   begin
      Put (W, '<');
      Put (W, Tag);
      Put (W, '>');
      Put_Escaped (W, Text, S);
      Put (W, "</");
      Put (W, Tag);
      Put (W, '>');
   end Element;

   procedure Write (W : in out Json.Writer; Text : String; Values : Value_Array) is
      Bank : constant Boolean := Is_Bank (Text, Values);
   begin
      Put (W, "<?xml version=""1.0"" encoding=""UTF-8"" standalone=""no""?>");
      Put (W, ASCII.CR & ASCII.LF);
      Put (W, "<?OFX OFXHEADER=""200"" VERSION=""220"" SECURITY=""NONE"" OLDFILEUID=""NONE"" ");
      Put (W, "NEWFILEUID=""");
      Put_Escaped (W, Text, Values (Newfileuid));
      Put (W, """?>");

      Put (W, "<OFX><SIGNONMSGSRQV1><SONRQ>");
      Element (W, "DTCLIENT", Text, Values (Dtclient));
      Element (W, "USERID", Text, Values (Userid));
      Element (W, "USERPASS", Text, Values (Userpass));
      Put (W, "<LANGUAGE>ENG</LANGUAGE>");
      if not Is_Empty (Values (Org)) then
         Put (W, "<FI>");
         Element (W, "ORG", Text, Values (Org));
         if not Is_Empty (Values (Fid)) then
            Element (W, "FID", Text, Values (Fid));
         end if;
         Put (W, "</FI>");
      end if;
      Element (W, "APPID", Text, Values (Appid));
      Element (W, "APPVER", Text, Values (Appver));
      if not Is_Empty (Values (Clientuid)) then
         Element (W, "CLIENTUID", Text, Values (Clientuid));
      end if;
      Put (W, "</SONRQ></SIGNONMSGSRQV1>");

      if Bank then
         Put (W, "<BANKMSGSRQV1><STMTTRNRQ>");
         Element (W, "TRNUID", Text, Values (Trnuid));
         Put (W, "<STMTRQ><BANKACCTFROM>");
         Element (W, "BANKID", Text, Values (Bankid));
         Element (W, "ACCTID", Text, Values (Acctid));
         Element (W, "ACCTTYPE", Text, Values (Accttype));
         Put (W, "</BANKACCTFROM>");
      else
         Put (W, "<CREDITCARDMSGSRQV1><CCSTMTTRNRQ>");
         Element (W, "TRNUID", Text, Values (Trnuid));
         Put (W, "<CCSTMTRQ><CCACCTFROM>");
         Element (W, "ACCTID", Text, Values (Acctid));
         Put (W, "</CCACCTFROM>");
      end if;
      Put (W, "<INCTRAN>");
      if not Is_Empty (Values (Dtstart)) then
         Element (W, "DTSTART", Text, Values (Dtstart));
      end if;
      if not Is_Empty (Values (Dtend)) then
         Element (W, "DTEND", Text, Values (Dtend));
      end if;
      Put (W, "<INCLUDE>Y</INCLUDE></INCTRAN>");
      Put (W, (if Bank then "</STMTRQ></STMTTRNRQ></BANKMSGSRQV1></OFX>"
               else "</CCSTMTRQ></CCSTMTTRNRQ></CREDITCARDMSGSRQV1></OFX>"));
   end Write;

end Ofx.Requests;
