--  The type of each stored element and the check applied when it is stored.
--  Lengths follow the OFX specification and the columns of Pacioli's ofx
--  schema; enumerations are the values those columns accept.

with Ofx.Model; use Ofx.Model;

package Ofx.Values with SPARK_Mode is

   type Value_Kind is
     (Text_Value,           --  character data, decoded
      Date_Value,           --  Ofx.Dates
      Amount_Value,         --  Ofx.Decimals
      Number_Value,         --  unsigned digits, written as a JSON number
      Severity_Value,
      Trntype_Value,
      Accttype_Value,
      Correctaction_Value,
      Inv401k_Value,
      Code_Value);          --  three capital letters: ISO 4217 or ISO 639

   type Rule is record
      Kind       : Value_Kind := Text_Value;
      Max_Length : Positive := 1;   --  characters, or digits for numbers
   end record;

   function Status_Rule (F : Status_Field) return Rule is
     (case F is
         when Code     => (Number_Value, 6),
         when Severity => (Severity_Value, 5),
         when Message  => (Text_Value, 255));

   function Signon_Rule (F : Signon_Field) return Rule is
     (case F is
         when Dtserver => (Date_Value, 1),
         when Language => (Code_Value, 3),
         when Org      => (Text_Value, 32),
         when Fid      => (Text_Value, 32));

   function Statement_Rule (F : Statement_Field) return Rule is
     (case F is
         when Trnuid   => (Text_Value, 36),
         when Curdef   => (Code_Value, 3),
         when Bankid   => (Text_Value, 9),
         when Branchid => (Text_Value, 22),
         when Acctid   => (Text_Value, 22),
         when Accttype => (Accttype_Value, 10),
         when Acctkey  => (Text_Value, 22),
         when Dtstart | Dtend | Ledger_Dtasof | Avail_Dtasof => (Date_Value, 1),
         when Ledger_Balamt | Avail_Balamt => (Amount_Value, 1));

   function Transaction_Rule (F : Transaction_Field) return Rule is
     (case F is
         when Trntype       => (Trntype_Value, 11),
         when Dtposted | Dtuser | Dtavail => (Date_Value, 1),
         when Trnamt | Currate => (Amount_Value, 1),
         when Fitid         => (Text_Value, 255),
         when Correctfitid  => (Text_Value, 255),
         when Correctaction => (Correctaction_Value, 7),
         when Srvrtid       => (Text_Value, 10),
         when Checknum      => (Text_Value, 12),
         when Refnum        => (Text_Value, 32),
         when Sic           => (Number_Value, 6),
         when Payeeid       => (Text_Value, 12),
         when Name          => (Text_Value, 32),
         when Memo          => (Text_Value, 255),
         when Inv401ksource => (Inv401k_Value, 13),
         when Cursym        => (Code_Value, 3));

   --  Whether Text (S) is a valid non-empty value under R.
   function Valid (Text : String; S : Span; R : Rule; Encoding : Charset) return Boolean
   with Pre => Text'First = 1 and then Text'Last <= Max_Input and then Within (S, Text);

end Ofx.Values;
